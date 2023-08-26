FROM python:3.10

RUN pip install jupyter_kernel_gateway

EXPOSE 8888

CMD ["jupyter", "kernelgateway", "--KernelGatewayApp.ip='0.0.0.0'", "--KernelGatewayApp.port=8888"]
