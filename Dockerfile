# Use an official Python runtime as a parent image
FROM python:3.11-slim-buster

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Update package lists, clean the cache, and remove temporary files
RUN apt-get update && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install wkhtmltopdf with no-install-recommends to reduce size
RUN apt-get update && apt-get install -y --no-install-recommends wkhtmltopdf

# Clean up again after installation to further reduce image size
RUN apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8989 available to the world outside this container
EXPOSE 8989

# Define environment variable
#ENV NAME Poliswag

# Run app.py when the container launches
CMD ["python", "-u", "./main.py"]
