import re
import xml.etree.ElementTree as ET

content = """
Here is my plan:
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
    print("Full block starts with:", xml_full_trigger_block[:50])
    try:
        ET.fromstring(xml_full_trigger_block)
        print("Parse SUCCESS")
    except Exception as e:
        print("Parse ERROR:", e)
