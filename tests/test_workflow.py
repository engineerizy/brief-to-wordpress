from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient
import httpx

from brief_to_wordpress.api import create_app
from brief_to_wordpress.integrations import OpenAIPlanner, Settings, WordPress, fixture
from brief_to_wordpress.store import StateConflict


class WorkflowTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = str(Path(directory.name) / "test.sqlite3")
        self.brief = fixture("brief.json")

    def test_demo_review_and_lost_reply_recovery(self):
        with TestClient(create_app(Settings(database=self.database))) as client:
            self.assertEqual(client.get("/").status_code, 200)
            response = client.post("/api/runs", json={"brief": self.brief})
            self.assertEqual(response.status_code, 201, response.text)
            run = response.json()
            path = f"/api/runs/{run['id']}"
            self.assertEqual(run["state"], "review")
            self.assertEqual(run["planner"], "Sample planner")
            self.assertIn("MCP server", run["events"][1]["detail"])
            self.assertIn('href="/contact"', run["blocks"])
            self.assertIn("<!-- wp:heading -->", run["blocks"])
            self.assertEqual(client.post(path + "/deliver", json={}).status_code, 409)
            self.assertEqual(client.post(path + "/approve", json={"revision": "0" * 64}).status_code, 409)
            self.assertEqual(
                client.post(path + "/approve", json={"revision": run["revision"]}).json()["state"],
                "approved",
            )
            unknown = client.post(path + "/deliver", json={"simulate_lost_reply": True}).json()
            self.assertEqual(unknown["state"], "needs_check")
            self.assertEqual(client.post(path + "/deliver", json={}).status_code, 409)
            recovered = client.post(path + "/reconcile", json={}).json()
            self.assertEqual(recovered["state"], "draft_created")
            self.assertIn("Found existing draft", recovered["events"][-1]["detail"])
            saved = client.get(recovered["draft"]["url"]).json()
            self.assertEqual(saved["content"], run["blocks"])
            self.assertEqual(saved["status"], "draft")
            self.assertEqual(client.post(path + "/deliver", json={}).json()["draft"], recovered["draft"])
            self.assertEqual(client.get("/api/sample-drafts/2").status_code, 404)

    def test_live_integrations_send_reviewed_draft_once(self):
        calls = []
        remote_draft = {}
        design = fixture("design.json")
        plan = {
            "title": "A reviewed launch page",
            "sections": [
                {"component": "hero", "heading": "Website launch", "body": "A site your team can update.", "points": []},
                {"component": "benefits", "heading": "Deliverables", "body": "Included in the handover.", "points": self.brief["points"]},
                {"component": "cta", "heading": "Start your brief", "body": "Book a consultation.", "points": []},
            ],
        }

        def tool_response(name, arguments):
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call_{name}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}}],
            })

        def respond(request):
            host = request.url.host
            calls.append((request.method, host))
            if host == "api.figma.com":
                self.assertEqual(request.url.path, "/v1/files/DemoKey123/nodes")
                self.assertEqual(request.url.params["ids"], "1:1")
                self.assertEqual(request.headers["X-Figma-Token"], "fixture-token")
                return httpx.Response(200, json=design)
            if host == "api.openai.com":
                body = json.loads(request.content)
                self.assertEqual(request.url.path, "/v1/chat/completions")
                self.assertEqual(request.headers["Authorization"], "Bearer fixture-key")
                tool = body["tools"][0]["function"]["name"]
                self.assertEqual(body["tool_choice"]["function"]["name"], tool)
                if tool == "read_component_library":
                    return tool_response(tool, {})
                self.assertEqual(tool, "submit_page_plan")
                tool_message = body["messages"][-1]
                self.assertEqual(tool_message["tool_call_id"], "call_read_component_library")
                catalog = json.loads(tool_message["content"])
                self.assertEqual([item["id"] for item in catalog["components"]], ["hero", "benefits", "cta"])
                return tool_response(tool, plan)
            self.assertEqual(host, "wordpress.example")
            self.assertEqual(request.url.path, "/wp-json/wp/v2/posts")
            self.assertTrue(request.headers["Authorization"].startswith("Basic "))
            if request.method == "POST":
                data = json.loads(request.content)
                self.assertEqual(data["status"], "draft")
                remote_draft.update(id=42, status="draft", slug=data["slug"], content={"raw": data["content"]})
                raise httpx.ReadTimeout("fixture: response lost after commit", request=request)
            self.assertEqual(request.url.params["slug"], remote_draft["slug"])
            return httpx.Response(200, json=[remote_draft])

        settings = Settings(
            self.database, OpenAIPlanner("fixture-key", "fixture-model"), "fixture-token",
            WordPress("https://wordpress.example", "editor", "fixture-password"),
        )
        with TestClient(create_app(settings, httpx.MockTransport(respond))) as client:
            response = client.post(
                "/api/runs", json={"brief": self.brief, "design": {"file_key": "DemoKey123", "node_id": "1:1"}}
            )
            self.assertEqual(response.status_code, 201, response.text)
            run = response.json()
            self.assertEqual(run["design"]["source"], "Figma REST API")
            self.assertEqual(run["planner"], "OpenAI")
            self.assertEqual(run["plan"], plan)
            path = f"/api/runs/{run['id']}"
            self.assertEqual(client.post(path + "/approve", json={"revision": run["revision"]}).status_code, 200)
            self.assertEqual(client.post(path + "/deliver", json={}).json()["state"], "needs_check")
            self.assertEqual(client.post(path + "/deliver", json={}).status_code, 409)
            confirmed = client.post(path + "/reconcile", json={}).json()
            self.assertEqual(confirmed["draft"]["id"], 42)
            self.assertEqual(remote_draft["content"]["raw"], run["blocks"])
            self.assertEqual(calls.count(("POST", "wordpress.example")), 1)

    def test_validation_and_escaped_markup(self):
        with TestClient(create_app(Settings(database=self.database))) as client:
            rejected = dict(self.brief, action_path="https://another.example/contact")
            self.assertEqual(client.post("/api/runs", json={"brief": rejected}).status_code, 422)
            escaped = dict(self.brief, goal="Copy with <strong>literal brackets</strong>.")
            response = client.post("/api/runs", json={"brief": escaped})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertIn("&lt;strong&gt;literal brackets&lt;/strong&gt;", response.json()["blocks"])

    def test_concurrent_delivery_claims_are_serialized(self):
        from brief_to_wordpress.store import Store

        store = Store(self.database)
        destination = "Local sample draft store"
        run = store.create(self.brief, {}, {"title": "Sample"}, "approved content", destination, "Sample", [])
        store.approve(run["id"], run["revision"])

        def claim(_):
            try:
                return store.claim_delivery(run["id"], destination)
            except StateConflict:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim, range(4))), 1)
