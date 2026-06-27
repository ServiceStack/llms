#!/bin/bash

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
    echo "Error: ffmpeg is not installed. Please install it and try again."
    exit 1
fi

# Function to convert image to AVIF
convert_image() {
    local image_file="$1"
    local avif_file="${image_file%.*}.avif"
    
    # Check if the AVIF file already exists
    if [ ! -f "$avif_file" ]; then
        echo "Converting $image_file to $avif_file"
        ffmpeg -i "$image_file" -c:v libaom-av1 -crf 30 -cpu-used 4 -b:v 0 -still-picture 1 -hide_banner -v 24 -stats "$avif_file"
    else
        echo "Skipping $image_file, $avif_file already exists"
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
            convert_image "$image_file"
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

