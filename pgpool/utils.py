def parse_bool(val):
    if val is None:
        return False
    if val.lower() == 'yes' or val.lower() == 'true':
        return True
    return False