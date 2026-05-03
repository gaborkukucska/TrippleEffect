import re, html

xml_string = """<kickoff_plan>
  <roles>
    <role>Coder</role>
    <role>Technical_Writer</role>
  </roles>
  <tasks>
    <task id="task_1">Research existing Snake game implementations</task>
    <task id="task_2" depends_on="task_1">Setup project</task>
  </tasks>
</kickoff_plan>"""

def escape_tag_content(tag_name: str):
    def _replacer(m):
        attrs = m.group(1) or ""
        return f"<{tag_name}{attrs}>{html.escape(m.group(2))}</{tag_name}>"
    return _replacer

xml_full_trigger_block = xml_string
for tag_to_escape in ["task", "role"]:
    xml_full_trigger_block = re.sub(
        rf"<{tag_to_escape}(\s+[^>]*)?>([\s\S]*?)</{tag_to_escape}>",
        escape_tag_content(tag_to_escape),
        xml_full_trigger_block,
        flags=re.IGNORECASE
    )

print(xml_full_trigger_block)
