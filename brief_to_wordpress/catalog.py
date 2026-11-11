from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel


server = FastMCP("WordPress component library", log_level="WARNING")


class ComponentSpec(BaseModel):
    id: str
    purpose: str


class Catalog(BaseModel):
    version: int
    components: list[ComponentSpec]
    rules: list[str]


@server.tool(structured_output=True)
def read_component_library() -> Catalog:
    """Return the approved components and their content rules."""
    return Catalog.model_validate({
        "version": 1,
        "components": [
            {"id": "hero", "purpose": "Introduce the offer with a heading and one short paragraph."},
            {"id": "benefits", "purpose": "Explain the offer with two to four concrete points."},
            {"id": "cta", "purpose": "Invite the reader to take the action specified in the brief."},
        ],
        "rules": [
            "Use the component order in the design.",
            "Write plain text and keep claims grounded in the brief.",
            "The application renders the approved action label and internal path.",
        ],
    })


if __name__ == "__main__":
    server.run()
