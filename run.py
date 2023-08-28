from openbox import DockerBox  # Make sure your DockerBox is correctly imported
import subprocess

from printPosition.printPosition import printPosition as print
def run_docker_ps():
    """Runs the 'docker ps' command and prints its output."""
    try:
        result = subprocess.run(
            ["docker", "ps"], capture_output=True, text=True, check=True
        )
        print("Docker containers:")
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running 'docker ps': {e}")


def get_container_id():
    """Runs the 'docker ps' command and returns the container ID."""
    try:
        result = subprocess.run(
            ["docker", "ps"], capture_output=True, text=True, check=True
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:  # The first line is the header
            # Assuming container ID is the first column
            container_id = lines[1].split()[0]
            return container_id
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running 'docker ps': {e}")
        return None


def print_docker_logs(container_id):
    """Prints the logs of a specified Docker container."""
    try:
        result = subprocess.run(
            ["docker", "logs", container_id],
            capture_output=True,
            text=True,
            check=True,
        )
        print("Docker logs:")
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while getting Docker logs: {e}")


def session_restoring():
    session = DockerBox()
    session.start()

    # copying the kernel id and the session id
    kernel_id = session.kernel_id
    session_id = session.session_id
    
    print(f"Session ID: {session_id}")
    assert session_id is not None

    container_id = get_container_id()
    run_docker_ps()
    if container_id:
        print_docker_logs(container_id)

    # setting the value to the variable hello
    session.run("hello = 'Hello World!'")

    del session
    
    container_id = get_container_id()
    run_docker_ps()
    if container_id:
        print_docker_logs(container_id)

    try:
        restored_session = DockerBox.from_id(session_id=session_id)
        
        # updating the kernel id and reconnecting with the new function
        restored_session.update_kernal_data( kernel_id)
        restored_session.connect_kernel_id()

        # printing the preset value
        print(restored_session.run("print(hello)"))

        container_id = get_container_id()
        
        run_docker_ps()
        if container_id:
            print_docker_logs(container_id)

    except ValueError as e:
        print(f"An error occurred: {e}")
    finally:
        restored_session.stop()

        container_id = get_container_id()
        run_docker_ps()
        if container_id:
            print_docker_logs(container_id)


if __name__ == "__main__":
    session_restoring()
