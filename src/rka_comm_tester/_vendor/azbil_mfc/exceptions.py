"""Azbil MQV CPL 通讯异常定义."""


class CPLError(Exception):
    """CPL 协议基础异常."""


class CPLChecksumError(CPLError):
    """校验和错误."""


class CPLTimeoutError(CPLError):
    """通讯超时."""


class CPLFrameError(CPLError):
    """帧格式错误."""


class CPLDeviceError(CPLError):
    """设备返回错误码."""

    def __init__(self, error_code: str, message: str = ""):
        self.error_code = error_code
        super().__init__(message or f"设备返回错误码: {error_code}")


class CPLConnectionError(CPLError):
    """串口连接错误."""


class GasSafetyError(CPLError):
    """安全校验失败 - 拒绝执行不安全操作."""

    def __init__(self, safety_result=None, message: str = ""):
        self.safety_result = safety_result
        if safety_result and not message:
            violations_str = "; ".join(
                v.message for v in safety_result.violations if v.level == "ERROR"
            )
            message = f"安全校验失败: {violations_str}"
        super().__init__(message or "安全校验失败")


class RecipeValidationError(CPLError):
    """配方验证错误."""


class MixerError(CPLError):
    """混合器操作错误."""
