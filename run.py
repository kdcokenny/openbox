from openbox import DockerBox


def session_restoring():
    session = DockerBox()
    session.start()
    session_id = session.session_id
    kernel_id = session.kernel_id

    session.run("hello = 'Hello World!'")

    del session

    try:
        restored_session = DockerBox.from_id(
            session_id=session_id, kernel_id=kernel_id
        )
        print(restored_session.run("print(hello)"))

    finally:
        restored_session.stop()


if __name__ == "__main__":
    session_restoring()
