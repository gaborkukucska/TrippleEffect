import re
import xml.etree.ElementTree as ET
import html

content = """
<kickoff_plan>
  <roles>
    <role>Developer</role>
  </roles>
  <tasks>
    <task>Establish Core Game Logic – Define and implement the functions for the snake's movement (updating coordinates based on direction) and collision detection (with walls and itself).</task>
    <task>Implement HTML/CSS Layout – Create a basic web page structure containing a canvas element (<canvas id="gameCanvas">) for the game board and CSS for styling the canvas and text elements (score, title).</task>
  </tasks>
</kickoff_plan>
"""

trigger_tag = "kickoff_plan"
escaped_trigger_tag = re.escape(trigger_tag)
pattern_str = rf"(<\s*{escaped_trigger_tag}(\s+[^>]*)?>([\s\S]*?)</\s*{escaped_trigger_tag}\s*>)"

match = re.search(pattern_str, content, re.IGNORECASE | re.DOTALL)
if match:
    xml_full_trigger_block = match.group(1).strip()
    
    # Pre-process the XML to escape < and > inside <task> tags
    def escape_task_content(match):
        task_content = match.group(1)
        escaped_content = html.escape(task_content)
        return f"<task>{escaped_content}</task>"
        
    safe_xml = re.sub(r"<task>(.*?)</task>", escape_task_content, xml_full_trigger_block, flags=re.IGNORECASE | re.DOTALL)
    
    try:
        root = ET.fromstring(safe_xml)
        print("Parse SUCCESS")
        for task in root.findall(".//task"):
            print("Task:", html.unescape(task.text))
    except Exception as e:
        print("Parse ERROR:", e)
