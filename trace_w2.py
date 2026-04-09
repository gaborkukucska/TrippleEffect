with open("/home/tom/TrippleEffect/logs/app_20260408_233414_1395575.log", "r") as f:
    in_range = False
    for line in f:
        if "06:30:19,878" in line and "Agent W2: Status changed from 'processing' to 'idle'" in line:
            in_range = True
        if in_range:
            if "W2" in line or "PM" in line:
                if "ERROR" in line or "INFO" in line or "Constitutional" in line:
                    print(line.strip()[:150])
            if "06:34:23,401" in line and "Status changed from 'processing' to 'idle'" in line:
                break
