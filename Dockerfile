FROM python:3.10

WORKDIR /usr/src/app

# Install Jupyter Kernel Gateway and other packages
RUN pip install jupyter_kernel_gateway \
    numpy \
    pandas \
    matplotlib \
    seaborn \
    scikit-learn \
    yfinance \
    scipy \
    statsmodels \
    sympy \
    bokeh \
    plotly \
    dash \
    networkx

CMD ["jupyter", "kernelgateway", "--KernelGatewayApp.ip=0.0.0.0", "--KernelGatewayApp.port=8888", "--debug"]

