with open('/home/tom/TrippleEffect/logs/app_20260503_091423_2701031.log', 'r') as f:
    text = f.read()

parts = text.split('<kickoff_plan>')
if len(parts) > 1:
    print('<kickoff_plan>' + parts[-1].split('</kickoff_plan>')[0] + '</kickoff_plan>')
else:
    print("Not found")
