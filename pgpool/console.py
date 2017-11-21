# Helper function to calculate start and end line for paginated output
import logging
import math
import os
import platform
import shlex
import struct
import subprocess
import time
from datetime import datetime
from threading import Thread

from peewee import fn

from pgpool.models import Account, flaskDb
from pgpool.utils import rss_mem_size

default_log_level = 0

def input_processor(state):
    mainlog = logging.getLogger()
    global default_log_level

    while True:
        # Wait for the user to press a key.
        command = raw_input()

        if command == '':
            # Toggle between scouts and log view
            state['display'] = 'stats' if state['display'] == 'logs' else 'logs'

        # Disable logging if in fullscreen more
        if state['display'] == 'logs':
            mainlog.setLevel(default_log_level)
        else:
            mainlog.setLevel(logging.CRITICAL)


def print_status(initial_display):
    global status
    global default_log_level

    state = {
        'display': initial_display
    }

    default_log_level = logging.getLogger().getEffectiveLevel()
    if initial_display != 'logs':
        logging.getLogger().setLevel(logging.CRITICAL)
    
    # Start another thread to get user input.
    t = Thread(target=input_processor,
               name='input_processor',
               args=(state,))
    t.daemon = True
    t.start()

    while True:
        time.sleep(5)
        if state['display'] == 'logs':
            continue

        lines = []

        if state['display'] == 'stats':
            print_stats(lines)

        # Print lines
        os.system('cls' if os.name == 'nt' else 'clear')
        print ('\n'.join(lines)).encode('utf-8')


def print_stats(lines):
    lines.append("Mem Usage: {}".format(rss_mem_size()))

    try:
        total = Account.select(fn.COUNT(Account.username)).scalar()
        lines.append("Total Accounts: {}\n".format(total))

        lines.append("Condition     | L1-29   | L30+    | unknown | TOTAL")

        print_stats_line(lines, "ALL", "1")
        print_stats_line(lines, "Unknown / New", "level is null")
        print_stats_line(lines, "In Use", "system_id is not null")
        print_stats_line(lines, "Good", "banned = 0 and shadowbanned = 0")
        print_stats_line(lines, "Only Blind", "banned = 0 and shadowbanned = 1")
        print_stats_line(lines, "Banned", "banned = 1")
        print_stats_line(lines, "Captcha", "captcha = 1")
    except Exception as e:
        lines.append("Exception: {}".format(e))


def print_stats_line(lines, name, condition):
    cursor = flaskDb.database.execute_sql('''
        select (case when level < 30 then "low" when level >= 30 then "high" else "unknown" end) as category, count(*) from account
        where {}
        group by category
    '''.format(condition))
    low = 0
    high = 0
    unknown = 0
    for row in cursor.fetchall():
        if row[0] == 'low':
            low = row[1]
        elif row[0] == 'high':
            high = row[1]
        elif row[0] == 'unknown':
            unknown = row[1]
    lines.append("{:<13} | {:>7} | {:>7} | {:>7} | {:>7}".format(name, low, high, unknown, low + high + unknown))


def print_lines(lines, print_entity, entities, addl_lines, state):
    # Pagination.
    start_line, end_line, total_pages = calc_pagination(len(entities), addl_lines,
                                                        state)

    current_line = 0
    for e in entities:
        # Skip over items that don't belong on this page.
        current_line += 1
        if current_line < start_line:
            continue
        if current_line > end_line:
            break

        lines.append(print_entity(current_line, e))

    return total_pages


def calc_pagination(total_rows, non_data_rows, state):
    width, height = get_terminal_size()
    # Title and table header is not usable space
    usable_height = height - non_data_rows
    # Prevent people running terminals only 6 lines high from getting a
    # divide by zero.
    if usable_height < 1:
        usable_height = 1

    total_pages = int(math.ceil(total_rows / float(usable_height)))

    # Prevent moving outside the valid range of pages.
    if state['page'] > total_pages:
        state['page'] = total_pages
    if state['page'] < 1:
        state['page'] = 1

    # Calculate which lines to print (1-based).
    start_line = usable_height * (state['page'] - 1) + 1
    end_line = start_line + usable_height - 1

    return start_line, end_line, total_pages


def hr_tstamp(tstamp):
    if isinstance(tstamp, float):
        return datetime.fromtimestamp(tstamp).strftime("%H:%M:%S")
    else:
        return tstamp


def get_terminal_size():
    """ getTerminalSize()
     - get width and height of console
     - works on linux,os x,windows,cygwin(windows)
     originally retrieved from:
     http://stackoverflow.com/questions/566746/how-to-get-console-window-width-in-python
    """
    current_os = platform.system()
    tuple_xy = None
    if current_os == 'Windows':
        tuple_xy = _get_terminal_size_windows()
        if tuple_xy is None:
            tuple_xy = _get_terminal_size_tput()
            # Needed for window's python in cygwin's xterm!
    if current_os in ['Linux', 'Darwin'] or current_os.startswith('CYGWIN'):
        tuple_xy = _get_terminal_size_linux()
    if tuple_xy is None:
        tuple_xy = (80, 25)      # Default value.
    return tuple_xy


def _get_terminal_size_windows():
    try:
        from ctypes import windll, create_string_buffer
        # stdin handle is -10
        # stdout handle is -11
        # stderr handle is -12
        h = windll.kernel32.GetStdHandle(-12)
        csbi = create_string_buffer(22)
        res = windll.kernel32.GetConsoleScreenBufferInfo(h, csbi)
        if res:
            (bufx, bufy, curx, cury, wattr,
             left, top, right, bottom,
             maxx, maxy) = struct.unpack("hhhhHhhhhhh", csbi.raw)
            sizex = right - left + 1
            sizey = bottom - top + 1
            return sizex, sizey
    except:
        pass


def _get_terminal_size_tput():
    # Get terminal width.
    # src: How do I find the width & height of a terminal window?
    # url: http://stackoverflow.com/q/263890/1706351
    try:
        cols = int(subprocess.check_call(shlex.split('tput cols')))
        rows = int(subprocess.check_call(shlex.split('tput lines')))
        return (cols, rows)
    except:
        pass


def _get_terminal_size_linux():
    def ioctl_GWINSZ(fd):
        try:
            import fcntl
            import termios
            cr = struct.unpack('hh',
                               fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))
            return cr
        except:
            pass
    cr = ioctl_GWINSZ(0) or ioctl_GWINSZ(1) or ioctl_GWINSZ(2)
    if not cr:
        try:
            fd = os.open(os.ctermid(), os.O_RDONLY)
            cr = ioctl_GWINSZ(fd)
            os.close(fd)
        except:
            pass
    if not cr:
        try:
            cr = (os.environ['LINES'], os.environ['COLUMNS'])
        except:
            return None
    return int(cr[1]), int(cr[0])
