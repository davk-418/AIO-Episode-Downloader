import subprocess
import os
import re
import requests
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC

# Prompt the user for the download directory at the start
base_directory = input("Enter the directory where you want to download files (e.g., C:\\Users\\YourName\\Downloads): ").strip()

# Validate the directory
if not os.path.isdir(base_directory):
    print(f"The directory '{base_directory}' does not exist. Please create it or enter a valid path.")
    exit(1)

# Ask if the user wants to embed an image
embed_image = input("Do you want to embed an image into the MP3 file? (slower) (y/n): ").strip().lower()

def extract_filename_from_url(url):
    """Extract the filename from the URL."""
    match = re.search(r"(?:FileGroup1/|episode/)([^/]+?)(?=_club|_Mp4|\.|$)", url)
    if match:
        return match.group(1)  # Return the extracted filename part
    else:
        return "downloaded_file"  # Fallback name if no match

def download_image_with_cmd(image_url, image_filename):
    """Download the image using a cmd command."""
    print(f"Downloading image from {image_url}...")
    try:
        # Use curl or wget to download the image to the current directory
        download_command = f'curl -o "{os.path.join(base_directory, image_filename)}" "{image_url}"'
        subprocess.run(download_command, shell=True, check=True)
        print(f"Image downloaded successfully: {image_filename}")
    except subprocess.CalledProcessError as e:
        print(f"Error downloading image: {e}")
        return None

def reencode_mp3(mp3_file_path):
    """Re-encode the MP3 file using ffmpeg to ensure it's properly structured."""
    try:
        reencoded_file_path = mp3_file_path.replace(".mp3", "_reencoded.mp3")
        ffmpeg_command = f'ffmpeg -i "{mp3_file_path}" -acodec libmp3lame -ab 192k "{reencoded_file_path}"'
        subprocess.run(ffmpeg_command, shell=True, check=True)
        print(f"Re-encoded MP3 file created: {reencoded_file_path}")
        return reencoded_file_path
    except subprocess.CalledProcessError as e:
        print(f"Error during MP3 re-encoding: {e}")
        return None

def embed_image_in_mp3(mp3_file_path, image_file_path):
    """Embeds the image into the MP3 file as album art."""
    if not os.path.isfile(mp3_file_path):
        print(f"Error: MP3 file '{mp3_file_path}' does not exist.")
        return
    
    if not os.path.isfile(image_file_path):
        print(f"Error: Image file '{image_file_path}' does not exist.")
        return
    
    try:
        # Load the MP3 file
        audio_file = MP3(mp3_file_path, ID3=ID3)

        # Read the image file as binary
        with open(image_file_path, "rb") as img_file:
            image_data = img_file.read()

        # Create or modify the ID3 tag
        audio_file.tags.add(APIC(
            encoding=3,  # Encoding type (3 = UTF-8)
            mime="image/jpeg",  # MIME type for the image (could be image/png as well)
            type=3,  # Type (3 = front cover)
            desc="Cover",
            data=image_data
        ))

        # Save the changes to the MP3 file
        audio_file.save()
        print(f"Image embedded into {mp3_file_path} successfully!")
    except Exception as e:
        print(f"Error while embedding image: {e}")

def modify_and_run_in_cmd():
    print("Paste the cURL below and press Enter (leave a blank line to finish):")
    lines = []
    while True:
        line = input()
        if line.strip() == "":  # Stop when a blank line is entered
            break
        lines.append(line)

    # Extract the URL from the cURL (the URL will be in quotes after 'https://' or 'http://')
    url_match = re.search(r'(https?://[^\s]+)', ' '.join(lines))
    if url_match:
        url = url_match.group(0)
        # Extract the filename from the URL
        output_filename = extract_filename_from_url(url)
    else:
        print("Error: No URL found in the cURL.")
        return

    # Replace hyphens in the filename with underscores
    output_filename = output_filename.replace('-', '_')

    # Ensure the filename ends with '.mp3'
    if not output_filename.endswith(".mp3"):
        output_filename += ".mp3"

    # Modify each line
    modified_lines = []
    for line in lines:
        if '-H ^"range: bytes' in line:
            line = '-H ^"range: bytes=-^" ^'  # Replace the targeted line
        modified_lines.append(line)

    # Add the output filename to the last line
    if modified_lines:
        modified_lines[-1] += f' -o "{output_filename}"'

    # Combine lines into a script that maintains spacing
    final_command = "\n".join(modified_lines)

    # Write a batch script to execute commands in CMD
    batch_script = f"""
    @echo off
    cd /d {base_directory}
    {final_command}
    """

    # Save the batch script to a temporary file
    batch_file_path = os.path.join(base_directory, "run_command.bat")
    with open(batch_file_path, "w") as batch_file:
        batch_file.write(batch_script)

    # Run the batch script
    print(f"\nRunning commands in Command Prompt from directory '{base_directory}'.")
    try:
        subprocess.run(["cmd", "/c", batch_file_path], shell=True)
    except Exception as e:
        print(f"Error while executing the command: {e}")
    finally:
        print("Done! Program made by NotKevin using ChatGPT :tomsmirk:")

    # Wait until the MP3 file is successfully downloaded before proceeding
    mp3_file_path = os.path.join(base_directory, output_filename)
    
    # Check if the MP3 file exists
    if not os.path.isfile(mp3_file_path):
        print(f"Error: The MP3 file '{output_filename}' was not downloaded.")
        return

    # Confirm MP3 file download success
    print(f"MP3 file downloaded successfully: {mp3_file_path}")

    # Re-encode the MP3 file to ensure it's well-formed if embedding image
    if embed_image == "y":
        reencoded_mp3 = reencode_mp3(mp3_file_path)
        if not reencoded_mp3:
            print("Failed to re-encode the MP3 file. Exiting.")
            return

        # Remove the original MP3 file after re-encoding
        os.remove(mp3_file_path)
        print(f"Original MP3 file removed: {mp3_file_path}")

        image_url = input("Enter the URL for a PNG or JPG image to embed as album art: ").strip()

        if image_url:
            # Extract the image filename from the URL
            image_filename = os.path.basename(image_url)
            
            # Download the image using cmd
            download_image_with_cmd(image_url, image_filename)

            # Path to the downloaded image
            image_file_path = os.path.join(base_directory, image_filename)

            # Embed the image into the re-encoded MP3 file
            embed_image_in_mp3(reencoded_mp3, image_file_path)

            # Delete the image file after embedding it
            os.remove(image_file_path)
            print(f"Image file removed: {image_file_path}")

        # Remove the '_reencoded' suffix from the re-encoded MP3 file
        if "_reencoded" in reencoded_mp3:
            final_mp3_path = reencoded_mp3.replace("_reencoded", "")
            os.rename(reencoded_mp3, final_mp3_path)
            print(f"Renamed re-encoded MP3 file to: {final_mp3_path}")
    else:
        print("MP3 file downloaded without embedding an image.")

if __name__ == "__main__":
    while True:
        modify_and_run_in_cmd()
        input("Press Enter to reset the program...")
