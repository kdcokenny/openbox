from openbox import DockerBox


def session_restoring():
    session = DockerBox()
    session.start()

    session_id = session.session_id
    kernel_id = session.kernel_id
    port = session.port
    print(session_id)
    assert session_id is not None

    session.run('hello = "Hello World!"')

    del session

    print(
        DockerBox.from_id(
            session_id=session_id, kernel_id=kernel_id, port=port
        ).run("print(hello)")
    )

    # DockerBox.from_id(session_id=session_id, kernel_id=kernel_id).stop()


if __name__ == "__main__":
    session_restoring()
