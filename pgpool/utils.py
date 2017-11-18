import os

import psutil


def parse_bool(val):
    if val is None:
        return False
    if val.lower() == 'yes' or val.lower() == 'true':
        return True
    return False


def rss_mem_size():
    process = psutil.Process(os.getpid())
    mem = process.memory_info().rss
    unit = 'bytes'
    if mem > 1024:
        unit = 'KB'
        mem /= 1024
    if mem > 1024:
        unit = 'MB'
        mem /= 1024
    if mem > 1024:
        unit = 'GB'
        mem /= 1024
    return "{:>4.1f} {}".format(mem, unit)
