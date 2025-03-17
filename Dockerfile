# Use an official Python runtime as a parent image
FROM python:3.11-slim-buster
# Set the working directory to /app
WORKDIR /app
# Copy the current directory contents into the container at /app
COPY . /app

# Install wkhtmltopdf and cleanup in the same RUN command to reduce image layers
RUN apt-get update && \
    apt-get install -y --no-install-recommends wkhtmltopdf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8989 available to the world outside this container
EXPOSE 8989

# Run app.py when the container launches
CMD ["python", "-u", "./main.py"]
