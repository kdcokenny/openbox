import asyncio

from openbox import DockerBox


def test_DockerBox():
    codebox = DockerBox()
    assert run_sync(codebox), "Failed to run sync codebox remotely"
    # assert asyncio.run(
    #     run_async(codebox)
    # ), "Failed to run async codebox remotely"


def test_localbox():
    codebox = DockerBox(local=True)
    assert run_sync(codebox), "Failed to run sync codebox locally"
    assert asyncio.run(
        run_async(codebox)
    ), "Failed to run async codebox locally"


def run_sync(codebox: DockerBox) -> bool:
    try:
        assert codebox.start() == "started"

        assert codebox.status() == "running"

        assert codebox.run("print('Hello World!')") == "Hello World!\n"

        file_name = "test_file.txt"
        assert file_name in str(codebox.upload(file_name, b"Hello World!"))

        assert codebox.download(file_name).content == b"Hello World!"

        package_name = "matplotlib"
        assert package_name in str(codebox.install(package_name))
        assert (
            "error"
            != codebox.run(
                "import matplotlib; print(matplotlib.__version__)"
            ).type
        )

        o = codebox.run(
            "import matplotlib.pyplot as plt;"
            "plt.plot([1, 2, 3, 4], [1, 4, 2, 3]); plt.show()"
        )
        assert o.type == "image/png"

    finally:
        assert codebox.stop() == "stopped"

    return True


async def run_async(codebox: DockerBox) -> bool:
    try:
        assert await codebox.astart() == "started"

        assert await codebox.astatus() == "running"

        assert await codebox.arun("print('Hello World!')") == "Hello World!\n"

        file_name = "test_file.txt"
        assert file_name in str(
            await codebox.aupload(file_name, b"Hello World!")
        )

        assert (await codebox.adownload(file_name)).content == b"Hello World!"

        package_name = "matplotlib"
        assert package_name in str(await codebox.ainstall(package_name))
        assert (
            "error"
            != (
                await codebox.arun(
                    "import matplotlib; print(matplotlib.__version__)"
                )
            ).type
        )

        o = await codebox.arun(
            "import matplotlib.pyplot as plt;"
            "plt.plot([1, 2, 3, 4], [1, 4, 2, 3]); plt.show()"
        )
        assert o.type == "image/png"

    finally:
        assert await codebox.astop() == "stopped"

    return True


if __name__ == "__main__":
    test_DockerBox()
