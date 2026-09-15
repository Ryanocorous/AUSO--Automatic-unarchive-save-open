def progress(line, source, destination):
    if line.startswith("Progress: ") and line.endswith("%"):
        return float(line[10:-1])
    return None
