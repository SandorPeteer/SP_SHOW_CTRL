#!/usr/bin/env python3
"""Screen detection using Quartz (macOS only)"""
try:
    from Quartz import CGDisplayBounds, CGMainDisplayID, CGGetActiveDisplayList

    # Get all active displays
    max_displays = 16
    (error, active_displays, display_count) = CGGetActiveDisplayList(max_displays, None, None)

    if error == 0:
        print(f"Found {display_count} display(s)")

        main_display = CGMainDisplayID()

        for i, display_id in enumerate(active_displays[:display_count]):
            bounds = CGDisplayBounds(display_id)
            is_main = " (main)" if display_id == main_display else ""
            print(f"Display {i}{is_main}: origin=({bounds.origin.x:.0f}, {bounds.origin.y:.0f}), size=({bounds.size.width:.0f}x{bounds.size.height:.0f})")

            # If this is not the main display, print its coordinates
            if display_id != main_display:
                print(f"COORDS:{bounds.origin.x:.0f},{bounds.origin.y:.0f}")
    else:
        print(f"Error getting display list: {error}")

except ImportError:
    print("Quartz module not available - install pyobjc-framework-Quartz")
    print("Run: pip3 install pyobjc-framework-Quartz")
