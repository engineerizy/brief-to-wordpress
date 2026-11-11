from html import escape

from .models import Brief, PagePlan


def wordpress_blocks(plan: PagePlan, brief: Brief) -> str:
    blocks = []
    for section in plan.sections:
        blocks.append(
            '<!-- wp:heading -->\n'
            f'<h2 class="wp-block-heading">{escape(section.heading)}</h2>\n'
            '<!-- /wp:heading -->'
        )
        blocks.append(f'<!-- wp:paragraph -->\n<p>{escape(section.body)}</p>\n<!-- /wp:paragraph -->')
        if section.points:
            items = "\n".join(
                f"<!-- wp:list-item -->\n<li>{escape(point)}</li>\n<!-- /wp:list-item -->"
                for point in section.points
            )
            blocks.append(f'<!-- wp:list -->\n<ul class="wp-block-list">\n{items}\n</ul>\n<!-- /wp:list -->')
        if section.component == "cta":
            blocks.append(
                '<!-- wp:buttons -->\n<div class="wp-block-buttons">'
                '<!-- wp:button -->\n<div class="wp-block-button">'
                f'<a class="wp-block-button__link wp-element-button" href="{escape(brief.action_path)}">'
                f"{escape(brief.action)}</a></div>\n"
                '<!-- /wp:button --></div>\n<!-- /wp:buttons -->'
            )
    return "\n\n".join(blocks)
