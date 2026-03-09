on run argv
	if (count of argv) is 0 then error "Missing command"
	
	set commandName to item 1 of argv
	set appName to "TradingView"
	
	if commandName is "activate" then
		tell application appName to activate
		tell application "System Events"
			tell process appName
				set frontmost to true
			end tell
		end tell
		delay 0.2
		return "activated"
	else if commandName is "keystroke" then
		if (count of argv) is less than 2 then error "Missing keystroke text"
		set textValue to item 2 of argv
		my ensureFrontmost(appName)
		tell application "System Events" to keystroke textValue
		return "typed"
	else if commandName is "keycode" then
		if (count of argv) is less than 2 then error "Missing keycode"
		set keyValue to (item 2 of argv) as integer
		set modifierText to ""
		if (count of argv) is greater than or equal to 3 then set modifierText to item 3 of argv
		my ensureFrontmost(appName)
		tell application "System Events"
			if modifierText is "command" then
				key code keyValue using {command down}
			else if modifierText is "shift" then
				key code keyValue using {shift down}
			else if modifierText is "option" then
				key code keyValue using {option down}
			else if modifierText is "control" then
				key code keyValue using {control down}
			else if modifierText is "command+shift" then
				key code keyValue using {command down, shift down}
			else
				key code keyValue
			end if
		end tell
		return "pressed"
	else if commandName is "scroll" then
		if (count of argv) is less than 2 then error "Missing scroll amount"
		set scrollAmount to (item 2 of argv) as integer
		my ensureFrontmost(appName)
		tell application "System Events"
			if scrollAmount < 0 then
				repeat (0 - scrollAmount) times
					key code 121
					delay 0.03
				end repeat
			else
				repeat scrollAmount times
					key code 116
					delay 0.03
				end repeat
			end if
		end tell
		return "scrolled"
	else if commandName is "next_row" then
		my ensureFrontmost(appName)
		tell application "System Events" to key code 125
		return "next_row"
	else if commandName is "prev_row" then
		my ensureFrontmost(appName)
		tell application "System Events" to key code 126
		return "prev_row"
	else if commandName is "page_down" then
		my ensureFrontmost(appName)
		tell application "System Events" to key code 121
		return "page_down"
	else if commandName is "page_up" then
		my ensureFrontmost(appName)
		tell application "System Events" to key code 116
		return "page_up"
	else if commandName is "open_symbol_search" then
		my ensureFrontmost(appName)
		tell application "System Events" to key code 3 using {command down}
		return "open_symbol_search"
	else if commandName is "screenshot" then
		if (count of argv) is less than 2 then error "Missing screenshot path"
		set outputPath to item 2 of argv
		do shell script "/usr/sbin/screencapture -x " & quoted form of outputPath
		return outputPath
	else if commandName is "window_title" then
		tell application "System Events"
			tell process appName
				if (count of windows) is 0 then return ""
				return name of window 1
			end tell
		end tell
	else
		error "Unknown command: " & commandName
	end if
end run

on ensureFrontmost(appName)
	tell application appName to activate
	tell application "System Events"
		tell process appName
			set frontmost to true
		end tell
	end tell
	delay 0.2
end ensureFrontmost
