# Basic docker image for PGPool
# Usage:
#   docker build -t PGPool
#   docker run -d --name PGPool 

FROM python:2.7-alpine

# Default port the webserver runs on
EXPOSE 4242

# Working directory for the application
WORKDIR /usr/src/app

# Set Entrypoint with hard-coded options
ENTRYPOINT ["python"]
CMD ["./pgpool.py"] 

# Install required system packages
RUN apk add --no-cache ca-certificates
RUN apk add --no-cache bash git openssh curl
RUN apk add --no-cache linux-headers

COPY requirements.txt /usr/src/app/

RUN pip install --no-cache-dir -r requirements.txt

# Copy everything to the working directory (Python files, templates, config) in one go.
COPY . /usr/src/app/
