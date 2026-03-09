import re
import html
import xml.etree.ElementTree as ET

xml_block = """<file_system><action>write</action><filepath>snake_game/index.html</filepath><content><!DOCTYPE html>
<html>
<head>
    <title>Snake Game</title>
</head>
<body>
    <canvas id="gameCanvas" width="400" height="400"></canvas>
    <script src="game.js"></script>
    if (a < b) {}
</body>
</html></content></file_system>"""

params = [{'name': 'action'}, {'name': 'filepath'}, {'name': 'content'}]

cleaned = xml_block
for param in params:
    param_name = param['name']
    
    def escape_match(match):
        inner_content = match.group(1)
        raw = html.unescape(inner_content)
        if raw.strip().startswith("<![CDATA[") and raw.strip().endswith("]]>"):
            raw = raw.strip()[9:-3]
        safe = raw.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f"<{param_name}>{safe}</{param_name}>"

    pattern = re.compile(rf"<{param_name}>(.*?)</{param_name}>", flags=re.IGNORECASE | re.DOTALL)
    cleaned = pattern.sub(escape_match, cleaned)

print("CLEANED: ")
print(cleaned)

try:
    root = ET.fromstring(cleaned)
    print("Parsed root:", root.tag)
    for child in root:
        print(child.tag, ":", html.unescape(child.text.strip()))
except Exception as e:
    print("Error:", e)
