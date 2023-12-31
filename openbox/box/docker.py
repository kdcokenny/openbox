"""Local implementation of CodeBox.

This is useful for testing and development.c In case you don't put an api_key,
this is the default CodeBox.
"""
import asyncio
import json
import os
import time
import docker
from typing import List, Optional, Union
from uuid import uuid4, UUID
import aiohttp
import requests  # type: ignore
from openbox.websockets.client import WebSocketClientProtocol
from openbox.websockets.client import connect as ws_connect
from openbox.websockets.exceptions import ConnectionClosedError
from openbox.websockets.sync.client import ClientConnection
from openbox.websockets.sync.client import connect as ws_connect_sync

from openbox.box import BaseBox
from openbox.config import settings
from openbox.schema import CodeBoxFile, CodeBoxOutput, CodeBoxStatus

DOCKER_IMAGE = "codebox"


class DockerBox(BaseBox):
    """DockerBox is a CodeBox implementation that
        runs code in a docker container.

    This is useful for both prod and testing.
    """

    _instance: Optional["DockerBox"] = None
    _jupyter_pids: List[int] = []

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        else:
            if settings.SHOW_INFO:
                print("INFO: Using the DockerBox\n")
        return cls._instance

    def __init__(self, /, **kwargs) -> None:
        super().__init__(session_id=kwargs.pop("session_id", None))
        self.port: int = 8888
        self.kernel_id: Optional[UUID] = kwargs.pop("kernel_id", None)
        self.ws: Union[WebSocketClientProtocol, ClientConnection, None] = None
        self.container: Optional[docker.models.containers.Container] = None
        self.docker_client = docker.from_env()
        self.aiohttp_session: Optional[aiohttp.ClientSession] = None
        self.last_used_time = time.time()

    # destructor
    def __del__(self):
        if self.aiohttp_session is not None:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.aiohttp_session.close())
            self.aiohttp_session = None

    # use function to update the last used time of the
    def use(self):
        self.last_used_time = time.time()

    def start(self) -> CodeBoxStatus:
        self.session_id = uuid4()
        os.makedirs(".codebox", exist_ok=True)
        self._check_port()

        if settings.VERBOSE:
            print("Starting kernel...")

        try:
            self.container = self.docker_client.containers.run(
                DOCKER_IMAGE,
                command=[
                    "jupyter",
                    "kernelgateway",
                    "--KernelGatewayApp.ip=0.0.0.0",
                    f"--KernelGatewayApp.port={self.port}",
                    "--debug",
                ],
                detach=True,
                ports={f"{self.port}/tcp": self.port},
                labels={"session_id": str(self.session_id)},
            )

        except docker.errors.ContainerError as e:
            print(f"Failed to start container: {e}")
            return CodeBoxStatus(status="error")

        while True:
            try:
                response = requests.get(self.kernel_url, timeout=270)
                if response.status_code == 200:
                    break
            except requests.exceptions.ConnectionError:
                pass
            if settings.VERBOSE:
                print("Waiting for kernel to start...")
            time.sleep(1)

        self._connect()
        return CodeBoxStatus(status="started")

    def _connect(self) -> None:
        if not self.kernel_id:
            response = requests.post(
                f"{self.kernel_url}/kernels",
                headers={"Content-Type": "application/json"},
                timeout=270,
            )
            self.kernel_id = response.json()["id"]

        if self.kernel_id is None:
            raise Exception("Could not start kernel")

        self.ws = ws_connect_sync(
            f"{self.ws_url}/kernels/{self.kernel_id}/channels"
        )

    def _check_port(self) -> int:
        max_port_limit = 65535
        initial_port = self.port

        while True:
            print(f"Checking port {self.port}...")
            try:
                response = requests.get(
                    f"http://localhost:{self.port}", timeout=5
                )
                print(f"Received status code: {response.status_code}")
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ):
                print(f"Port {self.port} is free.")
                return self.port
            else:
                print(f"Port {self.port} is occupied. Incrementing...")
                self.port += 1
                if self.port > max_port_limit:
                    self.port = initial_port
                    raise ValueError("Could not find an available port")

    async def astart(self) -> CodeBoxStatus:
        self.session_id = uuid4()
        os.makedirs(".codebox", exist_ok=True)
        await self._acheck_port()
        if settings.VERBOSE:
            print("Starting kernel asynchronously...")

        try:
            loop = asyncio.get_event_loop()
            self.container = await loop.run_in_executor(
                None,
                lambda: self.docker_client.containers.run(
                    DOCKER_IMAGE,
                    detach=True,
                    ports={f"{self.port}/tcp": self.port},
                    environment=["KernelGatewayApp.ip='0.0.0.0'"],
                    labels={"session_id": str(self.session_id)},
                ),
            )
        except docker.errors.ContainerError as e:
            print(f"Failed to start container: {e}")
            return CodeBoxStatus(status="error")

        time.sleep(2)
        self.aiohttp_session = aiohttp.ClientSession()
        while True:
            try:
                response = await self.aiohttp_session.get(self.kernel_url)
                if response.status == 200:
                    break
            except aiohttp.ClientConnectorError:
                pass
            if settings.VERBOSE:
                print("Waiting for kernel to start...")
            await asyncio.sleep(1)

        await self._aconnect()
        return CodeBoxStatus(status="started")

    async def _aconnect(self) -> None:
        if self.aiohttp_session is None:
            self.aiohttp_session = aiohttp.ClientSession()
        response = await self.aiohttp_session.post(
            f"{self.kernel_url}/kernels",
            headers={"Content-Type": "application/json"},
        )
        self.kernel_id = (await response.json())["id"]
        if self.kernel_id is None:
            raise Exception("Could not start kernel")
        self.ws = await ws_connect(
            f"{self.ws_url}/kernels/{self.kernel_id}/channels"
        )

    async def _acheck_port(self) -> None:
        try:
            if self.aiohttp_session is None:
                self.aiohttp_session = aiohttp.ClientSession()
            response = await self.aiohttp_session.get(
                f"http://localhost:{self.port}"
            )
        except aiohttp.ClientConnectorError:
            pass
        except aiohttp.ServerDisconnectedError:
            pass
        else:
            if response.status == 200:
                self.port += 1
                await self._acheck_port()

    def status(self) -> CodeBoxStatus:
        if not self.kernel_id:
            self._connect()

        self.use()
        return CodeBoxStatus(
            status="running"
            if self.kernel_id
            and requests.get(self.kernel_url, timeout=270).status_code == 200
            else "stopped"
        )

    async def astatus(self) -> CodeBoxStatus:
        if not self.kernel_id:
            await self._aconnect()
        self.use()
        return CodeBoxStatus(
            status="running"
            if self.kernel_id
            and self.aiohttp_session
            and (await self.aiohttp_session.get(self.kernel_url)).status == 200
            else "stopped"
        )

    def run(
        self,
        code: Optional[str] = None,
        file_path: Optional[os.PathLike] = None,
        retry=3,
    ) -> CodeBoxOutput:
        self.use()
        if not code and not file_path:
            raise ValueError("Code or file_path must be specified!")

        if code and file_path:
            raise ValueError("Can only specify code or the file to read_from!")

        if file_path:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()

        # run code in jupyter kernel
        if retry <= 0:
            raise RuntimeError("Could not connect to kernel")
        if not self.ws:
            self._connect()
            if not self.ws:
                raise RuntimeError(
                    "Jupyter not running. Make sure to start it first."
                )

        if settings.VERBOSE:
            print("Running code:\n", code)

        # send code to kernel
        self.ws.send(
            json.dumps(
                {
                    "header": {
                        "msg_id": (msg_id := uuid4().hex),
                        "msg_type": "execute_request",
                    },
                    "parent_header": {},
                    "metadata": {},
                    "content": {
                        "code": code,
                        "silent": False,
                        "store_history": True,
                        "user_expressions": {},
                        "allow_stdin": False,
                        "stop_on_error": True,
                    },
                    "channel": "shell",
                    "buffers": [],
                }
            )
        )
        self.use()
        result = ""
        while True:
            try:
                if isinstance(self.ws, WebSocketClientProtocol):
                    raise RuntimeError(
                        "Mixing asyncio and sync code is not supported"
                    )
                received_msg = json.loads(self.ws.recv())
            except ConnectionClosedError:
                self.start()
                return self.run(code, file_path, retry - 1)

            if (
                received_msg["header"]["msg_type"] == "stream"
                and received_msg["parent_header"]["msg_id"] == msg_id
            ):
                msg = received_msg["content"]["text"].strip()
                if "Requirement already satisfied:" in msg:
                    continue
                result += msg + "\n"
                if settings.VERBOSE:
                    print("Output:\n", result)

            elif (
                received_msg["header"]["msg_type"] == "execute_result"
                and received_msg["parent_header"]["msg_id"] == msg_id
            ):
                result += (
                    received_msg["content"]["data"]["text/plain"].strip()
                    + "\n"
                )
                if settings.VERBOSE:
                    print("Output:\n", result)

            elif received_msg["header"]["msg_type"] == "display_data":
                if "image/png" in received_msg["content"]["data"]:
                    return CodeBoxOutput(
                        type="image/png",
                        content=received_msg["content"]["data"]["image/png"],
                    )
                if "text/plain" in received_msg["content"]["data"]:
                    return CodeBoxOutput(
                        type="text",
                        content=received_msg["content"]["data"]["text/plain"],
                    )
                return CodeBoxOutput(
                    type="error",
                    content="Could not parse output",
                )
            elif (
                received_msg["header"]["msg_type"] == "status"
                and received_msg["parent_header"]["msg_id"] == msg_id
                and received_msg["content"]["execution_state"] == "idle"
            ):
                if len(result) > 500:
                    result = "[...]\n" + result[-500:]
                return CodeBoxOutput(
                    type="text",
                    content=result or "code run successfully (no output)",
                )

            elif (
                received_msg["header"]["msg_type"] == "error"
                and received_msg["parent_header"]["msg_id"] == msg_id
            ):
                error = (
                    f"{received_msg['content']['ename']}: "
                    f"{received_msg['content']['evalue']}"
                )
                if settings.VERBOSE:
                    print("Error:\n", error)
                return CodeBoxOutput(type="error", content=error)

    async def arun(
        self,
        code: str,
        file_path: Optional[os.PathLike] = None,
        retry=3,
    ) -> CodeBoxOutput:
        self.use()
        if file_path:
            raise NotImplementedError(
                "Reading from file is not supported in async mode"
            )

        # run code in jupyter kernel
        if retry <= 0:
            raise RuntimeError("Could not connect to kernel")
        if not self.ws:
            await self._aconnect()
            if not self.ws:
                raise RuntimeError(
                    "Jupyter not running. Make sure to start it first."
                )

        if settings.VERBOSE:
            print("Running code:\n", code)

        if not isinstance(self.ws, WebSocketClientProtocol):
            raise RuntimeError("Mixing asyncio and sync code is not supported")

        await self.ws.send(
            json.dumps(
                {
                    "header": {
                        "msg_id": (msg_id := uuid4().hex),
                        "msg_type": "execute_request",
                    },
                    "parent_header": {},
                    "metadata": {},
                    "content": {
                        "code": code,
                        "silent": False,
                        "store_history": True,
                        "user_expressions": {},
                        "allow_stdin": False,
                        "stop_on_error": True,
                    },
                    "channel": "shell",
                    "buffers": [],
                }
            )
        )
        self.use()
        result = ""
        while True:
            try:
                received_msg = json.loads(await self.ws.recv())
            except ConnectionClosedError:
                await self.astart()
                return await self.arun(code, file_path, retry - 1)

            if (
                received_msg["header"]["msg_type"] == "stream"
                and received_msg["parent_header"]["msg_id"] == msg_id
            ):
                msg = received_msg["content"]["text"].strip()
                if "Requirement already satisfied:" in msg:
                    continue
                result += msg + "\n"
                if settings.VERBOSE:
                    print("Output:\n", result)

            elif (
                received_msg["header"]["msg_type"] == "execute_result"
                and received_msg["parent_header"]["msg_id"] == msg_id
            ):
                result += (
                    received_msg["content"]["data"]["text/plain"].strip()
                    + "\n"
                )
                if settings.VERBOSE:
                    print("Output:\n", result)

            elif received_msg["header"]["msg_type"] == "display_data":
                if "image/png" in received_msg["content"]["data"]:
                    return CodeBoxOutput(
                        type="image/png",
                        content=received_msg["content"]["data"]["image/png"],
                    )
                if "text/plain" in received_msg["content"]["data"]:
                    return CodeBoxOutput(
                        type="text",
                        content=received_msg["content"]["data"]["text/plain"],
                    )
            elif (
                received_msg["header"]["msg_type"] == "status"
                and received_msg["parent_header"]["msg_id"] == msg_id
                and received_msg["content"]["execution_state"] == "idle"
            ):
                if len(result) > 500:
                    result = "[...]\n" + result[-500:]
                return CodeBoxOutput(
                    type="text",
                    content=result or "code run successfully (no output)",
                )

            elif (
                received_msg["header"]["msg_type"] == "error"
                and received_msg["parent_header"]["msg_id"] == msg_id
            ):
                error = (
                    f"{received_msg['content']['ename']}: "
                    f"{received_msg['content']['evalue']}"
                )
                if settings.VERBOSE:
                    print("Error:\n", error)
                return CodeBoxOutput(type="error", content=error)

    def upload(self, file_name: str, content: bytes) -> CodeBoxStatus:
        os.makedirs(".codebox", exist_ok=True)
        with open(os.path.join(".codebox", file_name), "wb") as f:
            f.write(content)

        return CodeBoxStatus(status=f"{file_name} uploaded successfully")

    async def aupload(self, file_name: str, content: bytes) -> CodeBoxStatus:
        return await asyncio.to_thread(self.upload, file_name, content)

    def download(self, file_name: str) -> CodeBoxFile:
        with open(os.path.join(".codebox", file_name), "rb") as f:
            content = f.read()

        return CodeBoxFile(name=file_name, content=content)

    async def adownload(self, file_name: str) -> CodeBoxFile:
        return await asyncio.to_thread(self.download, file_name)

    def install(self, package_name: str) -> CodeBoxStatus:
        self.run(f"!pip install -q {package_name}")
        self.restart()
        self.run(f"try:\n    import {package_name}\nexcept:\n    pass")
        return CodeBoxStatus(status=f"{package_name} installed successfully")

    async def ainstall(self, package_name: str) -> CodeBoxStatus:
        await self.arun(f"!pip install -q {package_name}")
        await self.arestart()
        await self.arun(f"try:\n    import {package_name}\nexcept:\n    pass")
        return CodeBoxStatus(status=f"{package_name} installed successfully")

    def list_files(self) -> List[CodeBoxFile]:
        return [
            CodeBoxFile(name=file_name, content=None)
            for file_name in os.listdir(".codebox")
        ]

    async def alist_files(self) -> List[CodeBoxFile]:
        return await asyncio.to_thread(self.list_files)

    def restart(self) -> CodeBoxStatus:
        self.use()
        return CodeBoxStatus(status="restarted")

    async def arestart(self) -> CodeBoxStatus:
        self.use()
        return CodeBoxStatus(status="restarted")

    def stop(self) -> CodeBoxStatus:
        if self.container is not None:
            self.container.stop()
            self.container.remove()
            self.container = None

        if self.ws is not None:
            try:
                if isinstance(self.ws, ClientConnection):
                    self.ws.close()
                else:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(self.ws.close())
            except ConnectionClosedError:
                pass
            self.ws = None

        return CodeBoxStatus(status="stopped")

        return CodeBoxStatus(status="stopped")

    async def astop(self) -> CodeBoxStatus:
        print(f"Stopping {self.session_id}")

        if self.container is not None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self.container.stop())
            await loop.run_in_executor(None, lambda: self.container.remove())
            self.container = None

        if self.ws is not None:
            try:
                await self.ws.close()
            except ConnectionClosedError:
                pass
            self.ws = None

        if self.aiohttp_session:
            await self.aiohttp_session.close()
            self.aiohttp_session = None

        return CodeBoxStatus(status="stopped")

    @classmethod
    def from_id(
        cls,
        session_id: Union[int, UUID],
        kernel_id: Optional[UUID],
        port: int,
        **kwargs,
    ) -> "DockerBox":
        if kernel_id:
            kwargs["kernel_id"] = (
                UUID(int=kernel_id)
                if isinstance(kernel_id, int)
                else kernel_id
            )

        kwargs["session_id"] = (
            UUID(int=session_id) if isinstance(session_id, int) else session_id
        )

        print(f"Kwargs: {kwargs}")

        instance = cls(**kwargs)

        instance.port = port

        container_list = docker.from_env().containers.list(
            filters={"label": f"session_id={kwargs['session_id']}"}
        )

        if container_list:
            instance.container = container_list[0]
        else:
            raise ValueError(
                f"No container found for session_id {kwargs['session_id']}"
            )

        return instance

    @property
    def kernel_url(self) -> str:
        """Return the url of the kernel."""
        return f"http://localhost:{self.port}/api"

    @property
    def ws_url(self) -> str:
        """Return the url of the websocket."""
        print(f"ws://localhost:{self.port}/api")
        return f"ws://localhost:{self.port}/api"
