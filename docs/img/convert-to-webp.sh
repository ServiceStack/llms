#!/bin/bash

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
    echo "Error: ffmpeg is not installed. Please install it and try again."
    exit 1
fi

# Function to convert image to WebP
convert_to_webp() {
    local image_file="$1"
    local webp_file="${image_file%.*}.webp"
    
    # Check if the WebP file already exists
    if [ ! -f "$webp_file" ]; then
        echo "Converting $image_file to $webp_file"
        ffmpeg -hide_banner -v 24 -stats -i "$image_file" "$webp_file"
    else
        echo "Skipping $image_file, $webp_file already exists"
    fi
}

# Function to process directories recursively
process_directory() {
    local dir="$1"
    
    # Change to the specified directory
    cd "$dir" || return
    
    # Process all image files in the current directory
    for image_file in *.png *.jpg *.jpeg; do
        # Check if the file exists (in case there are no matching files)
        if [ -f "$image_file" ]; then
            convert_to_webp "$image_file"
        fi
    done
    
    # Process all subdirectories
    for subdir in */; do
        if [ -d "$subdir" ]; then
            process_directory "$subdir"
        fi
    done
    
    # Return to the parent directory
    cd ..
}

# Start processing from the current directory
process_directory "."

echo "Conversion complete!"

