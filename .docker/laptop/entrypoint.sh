#!/bin/bash
set -e

# Setup Conda in bash script
source ~/miniconda3/etc/profile.d/conda.sh
conda activate robot

# Run the passed command
exec "$@"