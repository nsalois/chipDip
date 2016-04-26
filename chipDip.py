 #! /usr/bin/python


# C.H.I.P Dip is a modified version of Adafruits PythonWiFiRadio
# https://github.com/adafruit/Python-WiFi-Radio app written for chip
# the $9 computer getchip.com.
#
# This code is set up for use of Adafruits i2c/spi LCD backpack with a 16x2 LCD
# https://www.adafruit.com/products/292 connected via i2c to chips
# two wire interface. Pins "TWI1-SDA" and "TWI1-SCK".
#
# Because I am not using the Pi Plate this code was originally written for I chose to
# use chips "XIO" gpio for button input.
#
# To use the gpio you need both the CHIP_IO library from xtacocorex https://github.com/xtacocorex/CHIP_IO
# and his modified Adafruit_GPIO library https://github.com/xtacocorex/Adafruit_Python_GPIO


import atexit, pexpect, pickle, socket, time, os, subprocess
import Adafruit_CharLCD as LCD
import Adafruit_GPIO.MCP230xx as MCP
import Adafruit_GPIO as GPIO

mcp           = MCP.MCP23008()
gpio          = GPIO.get_platform_gpio()

# Constants:
DEBUG         = False
RGB_LCD       = False          # Set to 'True' if using color backlit LCD
HALT_ON_EXIT  = False          # Set to 'True' to shut down system when exiting
MAX_FPS       = 6 if RGB_LCD else 4 # Limit screen refresh rate for legibility
VOL_MIN       = -20
VOL_MAX       = 15
VOL_DEFAULT   = 0
LCD_ON_TIME   = 10             # Minuets for the LCD backlight to stay on. Push any button to wake.
SHUTDOWN_TIME = 3.0            # Time (seconds) to hold select button for shut down
UP            = "XIO-P3"
DOWN	      = "XIO-P4"
LEFT	      = "XIO-P5"
RIGHT	      = "XIO-P6"
SELECT 	      = "XIO-P7"
BUTTONS       = [UP, DOWN, LEFT, RIGHT, SELECT]  # List to make gpio initialization easier.
PICKLEFILE    = '/root/.config/pianobar/state.p'

# Global state:
volCur        = VOL_MIN        # Current volume
volNew        = VOL_DEFAULT    # 'Next' volume after interactions
volSpeed      = 1.0            # Speed of volume change (accelerates w/hold)
volSet        = False          # True if currently setting volume
paused        = False          # True if music is paused
staSel        = False          # True if selecting station
volTime       = 0              # Time of last volume button interaction
playMsgTime   = 0              # Time of last 'Playing' message display
staBtnTime    = 0              # Time of last button press on station menu
xTitle        = 16             # X position of song title (scrolling)
xInfo         = 16             # X position of artist/album (scrolling)
xStation      = 0              # X position of station (scrolling)
xTitleWrap    = 0
xInfoWrap     = 0
xStationWrap  = 0
songTitle     = ''
songTitleNoScroll = ''
artistNoScroll = ''
songInfo      = ''
stationNum    = 0               # Station currently playing
stationNew    = 0               # Station currently highlighted in menu
stationList   = ['']
stationIDs    = ['']
btnUp         = False
btnDown       = False
btnLeft       = False
btnRight      = False
btnSel        = False

# Char 7 gets reloaded for different
# modes. These are the bitmaps:
charSevenBitmaps = [
  [0b10000,  # Play (also selected station)
   0b11000,
   0b11100,
   0b11110,
   0b11100,
   0b11000,
   0b10000,
   0b00000],
  [0b11011,  # Pause
   0b11011,
   0b11011,
   0b11011,
   0b11011,
   0b11011,
   0b11011,
   0b00000],
  [0b00000,  # Next Track
   0b10100,
   0b11010,
   0b11101,
   0b11010,
   0b10100,
   0b00000,
   0b00000]]


# --------------------------------------------------------------------------
# Functions
# --------------------------------------------------------------------------

def clean_exit():              # Exit handler tries to leave LCD in a nice state.
    if DEBUG:
        print('cleanExit')

    for i in range(len(BUTTONS)):
        gpio.remove_event_detect(BUTTONS[i])

    gpio.cleanup()
    lcd.clear()
    time.sleep(1)
    lcd.set_backlight(1)

    if pianobar is not None:
        pianobar.kill(0)


def shutdown_menu():
    if DEBUG:
        print('ShutDown')

    battery_status()

    options = []
    options.append('Exit Pandora')
    options.append('Reboot')
    options.append('Shut down')
    choice = 0
    t = time.time()
    lcd.clear()
    lcd.message('Use LEFT, & U/D\n' + options[choice])

    while True:

        if (time.time() - t) > 60:  # In one minute, exits loop
            break

        if (time.time() - t) > 4:  # Delay so holding Sel too long dosen't exit loop before expected.
            if btnSel:
                break

        if btnUp:
            time.sleep(0.5)
            if choice < 2:
                choice += 1
            else:
                choice = 0
            lcd.clear()
            lcd.message('Use LEFT, & U/D\n' + options[choice])

        if btnDown:
            time.sleep(0.5)
            if choice > 0:
                choice -= 1
            else:
                choice = 2
            lcd.clear()
            lcd.message('Use LEFT, & U/D\n' + options[choice])

        if btnLeft:
            if choice == 0:  # Option to exit Pandora
                lcd.clear()
                lcd.message('Exiting Pandora')
                time.sleep(0.5)
                clean_exit()
                exit(0)
            elif choice == 1:  # Option to reboot chip
                lcd.clear()
                lcd.message('Rebooting')
                time.sleep(0.5)
                clean_exit()
                os.system("sudo reboot")
            elif choice == 2:  # Option to shut down chip
                lcd.clear()
                lcd.message('Shutting Down')
                time.sleep(1)
                lcd.message('Wait 30 seconds\nto unplug...')
                time.sleep(2)
                clean_exit()
                os.system("sudo shutdown now")


def battery_status():
    bStat = subprocess.check_output("sudo sh /home/chip/battery.sh", shell=True).strip('\n')
    lcd.clear()
    lcd.message('Battery Level'.center(16, ' '))
    lcd.set_cursor(0, 1)
    lcd.message(('= ' + bStat + '% Full').center(16, ' '))
    time.sleep(3)


def marquee(s, x, y, xWrap):                 # Draws song title or artist/album marquee at given position.
    if DEBUG:
        print('marquee')
    lcd.set_cursor(0, y)                      # Returns new position to avoid global uglies.
    if x > 0:                                # Initially scrolls in from right edge
        lcd.message(' ' * x + s[0:16-x])
    else:                                    # Then scrolls w/wrap indefinitely
        lcd.message(s[-x:16-x])
        if x < xWrap:
            return 0
    return x - 1


def draw_playing():
    if DEBUG:
        print('drawPlaying')
    lcd.create_char(7, charSevenBitmaps[0])
    lcd.set_cursor(0, 1)
    lcd.message('\x07 Playing'.center(16, ' '))
    return time.time()


def draw_paused():
    if DEBUG:
        print('drawPaused')
    lcd.create_char(7, charSevenBitmaps[1])
    lcd.set_cursor(0, 1)
    lcd.message('\x07 Paused'.center(16, ' '))


def draw_next_track():
    lcd.clear()
    lcd.create_char(7, charSevenBitmaps[2])
    lcd.set_cursor(0, 1)
    lcd.message('\x07 Next track... ')
    time.sleep(1)


def draw_stations(stationNew, listTop, xStation, staBtnTime):   # Draw station menu
    if DEBUG:
        print('drawStations')
    last = len(stationList)                                    # (overwrites fulls screen to facilitate scrolling)
    if last > 2:
        last = 2  # Limit stations displayed
    ret  = 0   # Default return value (for station scrolling)
    line = 0   # Line counter
    msg  = ''  # Clear output string to start
    for s in stationList[listTop:listTop+2]:  # For each station...
        sLen = len(s)  # Length of station name
        if (listTop + line) == stationNew:  # Selected station?
            msg += chr(7)  # Show selection cursor
            if sLen > 15:  # Is station name longer than line?
                if (time.time() - staBtnTime) < 0.5:
                    s2 = s[0:15]  # Just show start of line for half a sec
                else:
                    # After that, scrollinate
                    s2 = s + '   ' + s[0:15]
                    xStationWrap = -(sLen + 2)
                    s2 = s2[-xStation:15-xStation]
                    if xStation > xStationWrap:
                        ret = xStation - 1
            else:  # Short station name - pad w/spaces if needed
                s2 = s[0:15]
                if sLen < 15: s2 += ' ' * (15 - sLen)
        else:  # Not currently-selected station
            msg += ' '   # No cursor
            s2 = s[0:15]  # Clip or pad name to 15 chars
            if sLen < 15:
                s2 += ' ' * (15 - sLen)
        msg += s2  # Add station name to output message
        line += 1
        if line == last:
            break
        msg += '\n'  # Not last line - add newline
    lcd.set_cursor(0, 0)
    lcd.message(msg)
    return ret


def get_stations():
    if DEBUG:
        print('getStations')
    lcd.clear()
    lcd.message('Retrieving\nstation list...')
    pianobar.expect('Select station: ', timeout=20)   # 'before' is now string of stations I believe
    a     = pianobar.before.splitlines()              # break up into separate lines
    names = []
    ids   = []
    # Parse each line
    for b in a[:-1]:  # Skip last line (station select prompt)
        # Occasionally a queued up 'TIME: -XX:XX/XX:XX' string or
        # 'new playlist...' appears in the output.  Station list
        # entries have a known format, so it's straightforward to
        # skip these bogus lines.
        # print '\"{}\"'.format(b)
        if (b.find('playlist...') >= 0) or (b.find('Autostart') >= 0):
            continue
        # if b[0:5].find(':') >= 0: continue
        # if (b.find(':') >= 0) or (len(b) < 13): continue
        # Alternate strategy: must contain either 'QuickMix' or 'Radio':
        # Somehow the 'playlist' case would get through this check.  Buh?
        if b.find('Radio') or b.find('QuickMix'):
            id   = b[5:7].strip()
            name = b[13:].strip()
            # If 'QuickMix' found, always put at head of list
            if name == 'QuickMix':
                ids.insert(0, id)
                names.insert(0, name)
            else:
                ids.append(id)
                names.append(name)
    return names, ids


# --------------------------------------------------------------------------
# gpio callback functions
# --------------------------------------------------------------------------

def btn_up_pressed(self):
    global btnUp
    global backLightON
    global timeLightON
    if not backLightON:
        lcd.set_backlight(0)
        backLightON = True
        timeLightON = time.time()
    else:
        btnUp = True
        if DEBUG:
            print('UP')


def btn_down_pressed(self):
    global btnDown
    global backLightON
    global timeLightON
    if not backLightON:
        lcd.set_backlight(0)
        backLightON = True
        timeLightON = time.time()
    else:
        btnDown = True
        if DEBUG:
            print('DOWN')


def btn_left_pressed(self):
    global btnLeft
    global backLightON
    global timeLightON
    if not backLightON:
        lcd.set_backlight(0)
        backLightON = True
        timeLightON = time.time()
    else:
        btnLeft = True
        if DEBUG:
            print('LEFT')


def btn_right_pressed(self):
    global btnRight
    global backLightON
    global timeLightON
    if not backLightON:
        lcd.set_backlight(0)
        backLightON = True
        timeLightON = time.time()
    else:
        btnRight = True
        if DEBUG:
            print('RIGHT')


def btn_select_pressed(self):
    global btnSel
    global backLightON
    global timeLightON
    if not backLightON:
        lcd.set_backlight(0)
        backLightON = True
        timeLightON = time.time()
    else:
        btnSel = True
        if DEBUG:
            print('SELECT')


# --------------------------------------------------------------------------
# Initialization
# --------------------------------------------------------------------------

# In case of any errors we need to cleanup
# the gpio and lcd. Also if closed by ctrl-c.
atexit.register(clean_exit)

# Initialize GPIO for CHIP
for i in range(len(BUTTONS)):
    gpio.setup(BUTTONS[i], GPIO.IN)

# Set gpio event callbacks and debounce 350ms ( Seems to stop most edge
# bounce without delaying subsequent button presses to much. )
gpio.add_event_detect(UP, GPIO.FALLING, btn_up_pressed, 350)
gpio.add_event_detect(DOWN, GPIO.FALLING, btn_down_pressed, 350)
gpio.add_event_detect(LEFT, GPIO.FALLING, btn_left_pressed, 350)
gpio.add_event_detect(RIGHT, GPIO.FALLING, btn_right_pressed, 350)
gpio.add_event_detect(SELECT, GPIO.FALLING, btn_select_pressed, 350)

# Initialize I2C LCD for CHIP
lcd = LCD.Adafruit_CharLCD(rs=1, en=2, d4=3, d5=4, d6=5, d7=6, cols=16, lines=2, gpio=mcp, backlight=7)
lcd.set_backlight(0)
timeLightON = time.time()
time.sleep(0.1)

# Initial welcome message
lcd.clear()
lcd.message('     C.H.I.P.\n       Dip')
time.sleep(2.5)
lcd.clear()
lcd.message('    PIANOBAR\n Internet Radio')
time.sleep(2.5)
lcd.clear()

# Create volume bargraph custom characters (chars 0-5):
for i in range(6):
    bitmap = []
    bits = (255 << (5 - i)) & 0x1f
    for j in range(8):
        bitmap.append(bits)
    lcd.create_char(i, bitmap)

# Create up/down icon (char 6)
lcd.create_char(6,
  [0b00100,
   0b01110,
   0b11111,
   0b00000,
   0b00000,
   0b11111,
   0b01110,
   0b00100])

# By default, char 7 is loaded in 'pause' state
lcd.create_char(7, charSevenBitmaps[1])

# Get last-used volume and station name from pickle file
try:
    f = open(PICKLEFILE, 'rb')
    if DEBUG:
        print('pickOpen')
    v = pickle.load(f)
    f.close()
    volNew         = v[0]
    defaultStation = v[1]
except:
    if DEBUG:
        print('pickNOTopened')
    defaultStation = None

# Show IP address (if network is available).  System might be freshly
# booted and not have an address yet, so keep trying for a couple minutes
# before reporting failure.
t = time.time()
while True:
    if (time.time() - t) > 120:
        # No connection reached after 2 minutes
        if RGB_LCD:
            lcd.backlight(lcd.RED)
        lcd.message('Network is\nunreachable')
        time.sleep(30)
        exit(0)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 0))
        if RGB_LCD:
            lcd.backlight(lcd.GREEN)
        lcd.clear()
        lcd.message('My IP address is\n ' + s.getsockname()[0])
        time.sleep(2.5)
        break         # Success -- let's hear some music!
    except:
        time.sleep(1)  # Pause a moment, keep trying

# Launch pianobar as pi user (to use same config data, etc.) in background:
print('Spawning pianobar...')
pianobar = pexpect.spawn('pianobar')
print('Receiving station list...')
tout = pianobar.expect('Get stations... Ok.\r\n', timeout=120)  # tout is timeout return
if tout == 1:  # Timeout error handler
    lcd.clear()
    lcd.message('Station Timeout')
    time.sleep(5)
    shutdown_menu()
if tout == 0:  # No timeout, just a message to show on the LCD for a few seconds
    lcd.clear()
    lcd.message('Station Success!')
    time.sleep(3)
stationList, stationIDs = get_stations()
try:    # Use station name from last session
    stationNum = stationList.index(defaultStation)
except:  # Use first station in list
    stationNum = 0
print('Selecting station ' + stationIDs[stationNum])
pianobar.sendline(stationIDs[stationNum])


# --------------------------------------------------------------------------
# Main loop.  This is not quite a straight-up state machine; there's some
# persnickety 'nesting' and canceling among mode states, so instead a few
# global booleans take care of it rather than a mode variable.
# --------------------------------------------------------------------------

lastTime = 0

pattern_list = pianobar.compile_pattern_list(['SONG: ', 'STATION: ', 'TIME: '])

while pianobar.isalive():

    # Process all pending pianobar output
    while True:
        time.sleep(0.1)
        try:
            x = pianobar.expect(pattern_list, timeout=1)
            if x == 0:
                songTitle = ''
                songTitleNoScroll = ''
                artistNoScroll = ''
                songInfo   = ''
                xTitle     = 16
                xInfo      = 16
                xTitleWrap = 0
                xInfoWrap  = 0
                x = pianobar.expect(' \| ')
                if x == 0:  # Title | Artist | Album
                    print('Song: "{}"'.format(pianobar.before))
                    s = pianobar.before + '    '
                    songTitleNoScroll = s
                    n = len(s)
                    xTitleWrap = -n + 2
                    # 1+ copies + up to 15 chars for repeating scroll
                    songTitle = s * (1 + (16 / n)) + s[0:16]
                    x = pianobar.expect(' \| ')
                    if x == 0:
                        print('Artist: "{}"'.format(pianobar.before))
                        artist = pianobar.before
                        x = pianobar.expect('\r\n')
                        if x == 0:
                            print('Album: "{}"'.format(pianobar.before))
                            s = artist
                            artistNoScroll = s
                            n = len(s)
                            xInfoWrap = -n + 2
                            # 1+ copies + up to 15 chars for repeating scroll
                            songInfo = s * (2 + (16 / n)) + s[0:16]
            elif x == 1:
                x = pianobar.expect(' \| ')
                if x == 0:
                    print('Station: "{}"'.format(pianobar.before))
            elif x == 2:
                # Time doesn't include newline - prints over itself.
                x = pianobar.expect('\r', timeout=1)
                if x == 0:
                    if DEBUG:
                        print('Time: {}'.format(pianobar.before))
                # Periodically dump state (volume and station name)
                # to pickle file so it's remembered between each run.
                try:
                    f = open(PICKLEFILE, 'wb')
                    pickle.dump([volCur, stationList[stationNum]], f)
                    f.close()
                except:
                    pass
                # This break + changing the first pianobar.expect
                # timeout to =1 !=0 solved many random crashes for me.
                break
        except pexpect.EOF:
            if DEBUG:
                print('EOF')
                print(str(pianobar))
            break
        except pexpect.TIMEOUT:
            if DEBUG:
                print('TIMEOUT')
                print(str(pianobar))
            break

    # Certain button actions occur regardless of current mode.
    # Holding the select button (for shutdown) is a big one.
    if btnSel:
        if DEBUG:
            print('btnSel')
        btnSel = False
        t = time.time()                               # Start time of button press
        while 0 == gpio.input(SELECT):
            if btnSel:
                if DEBUG:
                    print('whileSELECT')
            # Wait for button release
            if (time.time() - t) >= SHUTDOWN_TIME:    # Extended hold?
                shutdown_menu()                       # We're outta here

        # If tapped, different things in different modes...
        if staSel:                   # In station select menu...
            pianobar.send('\n')      # Cancel station select
            staSel = False           # Cancel menu and return to
            if paused:
                draw_paused()        # play or paused state
        else:                        # In play/pause state...
            volSet = False           # Exit volume-setting mode (if there)
            paused = not paused      # Toggle play/pause
            pianobar.send('p')       # Toggle pianobar play/pause
            if paused:
                draw_paused()        # Display play/pause change
            else:
                playMsgTime = draw_playing()

    # Right button advances to next track in all modes, even paused,
    # when setting volume, in station menu, etc.
    elif btnRight:
        btnRight = False
        draw_next_track()
        if staSel:                   # Cancel station select, if there
            pianobar.send('\n')
            staSel = False
        paused = False               # Un-pause, if there
        volSet = False
        pianobar.send('n')
        time.sleep(1)                # Keep ">> Next Track" drawn for a sec

    # Left button enters station menu (if currently in play/pause state),
    # or selects the new station and returns.
    elif btnLeft:
        btnLeft = False
        staSel = not staSel  # Toggle station menu state
        if staSel:
            # Entering station selection menu.  Don't return to volume
            # select, regardless of outcome, just return to normal play.
            pianobar.send('s')
            lcd.create_char(7, charSevenBitmaps[0])
            volSet     = False
            cursorY    = 0   # Cursor position on screen
            stationNew = 0   # Cursor position in list
            listTop    = 0   # Top of list on screen
            xStation   = 0   # X scrolling for long station names
            # Just keep the list we made at start-up
            # stationList, stationIDs = getStations()
            staBtnTime = time.time()
            draw_stations(stationNew, listTop, 0, staBtnTime)
        else:
            # Just exited station menu with selection - go play.
            stationNum = stationNew # Make menu selection permanent
            print('Selecting station: "{}"'.format(stationIDs[stationNum]))
            pianobar.sendline(stationIDs[stationNum])
            paused = False

    # Up/down buttons either set volume (in play/pause) or select station
    elif btnUp or btnDown:

        if staSel:
            # Move up or down station menu
            if btnDown:
                if stationNew < (len(stationList) - 1):
                    stationNew += 1      # Next station
                    if cursorY < 1:
                        cursorY += 1     # Move cursor
                    else:
                        listTop += 1     # Y-scroll
                    xStation = 0         # Reset X-scroll
                    btnDown = False
            elif stationNew > 0:         # btnUp implied
                    stationNew -= 1      # Prev station
                    if cursorY > 0:
                        cursorY -= 1     # Move cursor
                    else:
                        listTop -= 1     # Y-scroll
                    xStation = 0         # Reset X-scroll
                    btnUp = False
            staBtnTime = time.time()     # Reset button time
            xStation = draw_stations(stationNew, listTop, 0, staBtnTime)
        else:
            if volSet is False:          # !Not in station menu
                lcd.set_cursor(0, 1)     # Just entering volume-setting mode; init display
                volCurI = int((volCur - VOL_MIN) + 0.5)
                n = int(volCurI / 5)
                s = (chr(6) + ' Volume ' +
                     chr(5) * n +        # Solid brick(s)
                     chr(volCurI % 5) +  # Fractional brick
                     chr(0) * (6 - n))   # Spaces
                lcd.message(s)
                volSet   = True
                volSpeed = 1.0
            # Volume-setting mode now active (or was already there);
            # act on button press.
            if btnUp:
                volNew = volCur + volSpeed
                if volNew > VOL_MAX: volNew = VOL_MAX
                btnUp = False
            else:
                volNew = volCur - volSpeed
                if volNew < VOL_MIN: volNew = VOL_MIN
                btnDown = False
            volTime = time.time()        # Time of last volume button press
            volSpeed *= 1.15             # Accelerate volume change

    # Other logic specific to unpressed buttons:
    else:
        if staSel:
            if DEBUG:
                print('staSEL')
            # In station menu, X-scroll active station name if long
            if len(stationList[stationNew]) > 15:
                xStation = draw_stations(stationNew, listTop, xStation, staBtnTime)
        elif volSet:
            volSpeed = 1.0               # Buttons released = reset volume speed
            # If no interaction in 4 seconds, return to prior state.
            # Volume bar will be erased by subsequent operations.
            if (time.time() - volTime) >= 4:
                volSet = False
                if paused:
                    draw_paused()

    # Various 'always on' logic independent of buttons
    if not staSel:  # Play/pause/volume: draw upper line (song title)
        if DEBUG:
            print('notstaSEL')
        if songTitle is not None:
            if DEBUG:
                print('songNOTnone')
            songTitleNoScroll = songTitleNoScroll.strip(' ')
            if len(songTitleNoScroll) > 16:
                xTitle = marquee(songTitle, xTitle, 0, xTitleWrap)
            else:
                lcd.set_cursor(0, 0)
                lcd.message(songTitleNoScroll.center(16, ' '))

        # Integerize current and new volume values
        volCurI = int((volCur - VOL_MIN) + 0.5)
        volNewI = int((volNew - VOL_MIN) + 0.5)
        volCur  = volNew
        # Issue change to pianobar
        if volCurI != volNewI:
            d = volNewI - volCurI
            if d > 0:
                s = ')' * d
            else:
                s = '(' * -d
            pianobar.send(s)

        # Draw lower line (volume or artist/album info):
        if volSet:
            if volNewI != volCurI:  # Draw only changes
                if volNewI > volCurI:
                    x = int(volCurI / 5)
                    n = int(volNewI / 5) - x
                    s = chr(5) * n + chr(volNewI % 5)
                else:
                    x = int(volNewI / 5)
                    n = int(volCurI / 5) - x
                    s = chr(volNewI % 5) + chr(0) * n
                lcd.set_cursor(x + 9, 1)
                lcd.message(s)
        elif paused is not True:
            if DEBUG:
                print('pausedNOTtrue')
            if (time.time() - playMsgTime) >= 3:
                # Display artist/album (rather than 'Playing')
                artistNoScroll = artistNoScroll.strip(' ')
                if len(artistNoScroll) > 16:
                    xInfo = marquee(songInfo, xInfo, 1, xInfoWrap)
                else:
                    lcd.set_cursor(0, 1)
                    lcd.message(artistNoScroll.center(16, ' '))

    # Turn off the lcd backlight if inactive for specified time
    if backLightON:
        if DEBUG:
            print('backlight' + str(backLightON))
            print(str(timeLightON))
            print(str(time.time()))
        t = time.time()
        if (t - timeLightON) >= (LCD_ON_TIME * 60):
            lcd.set_backlight(1)
            backLightON = False
            if DEBUG:
                print('backlight OFF')

    # Throttle frame rate, keeps screen legible
    while True:
        t = time.time()
        if (t - lastTime) > (1.0 / MAX_FPS):
            break
    lastTime = t
