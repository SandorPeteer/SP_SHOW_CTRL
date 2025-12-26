#!/usr/bin/env python3
"""Simple screen detection utility for macOS"""
import tkinter as tk

def detect_screens():
    """Detect all screens and return their geometries"""
    root = tk.Tk()
    root.withdraw()  # Hide the root window

    # Get primary screen dimensions
    primary_width = root.winfo_screenwidth()
    primary_height = root.winfo_screenheight()

    # Get virtual screen dimensions (all screens combined)
    root.update_idletasks()
    virtual_width = root.winfo_vrootwidth()
    virtual_height = root.winfo_vrootheight()

    print(f"Primary screen: {primary_width}x{primary_height}")
    print(f"Virtual screen: {virtual_width}x{virtual_height}")

    # If virtual is wider than primary, we have a second screen to the right
    if virtual_width > primary_width:
        second_left = primary_width
        second_top = 0
        second_width = virtual_width - primary_width
        second_height = primary_height  # Assume same height
        print(f"Second screen detected at: {second_left},{second_top} (size: {second_width}x{second_height})")
        print(f"COORDS:{second_left},{second_top}")
    else:
        print("No second screen detected")
        print("COORDS:NONE")

    root.destroy()

if __name__ == "__main__":
    detect_screens()
