"""Lightweight runtime i18n for tcc-g15.

Design notes
------------
* English is the source language and the default. Every key maps to an
  English string, so a missing translation degrades to English rather than
  showing a raw key.
* No Qt Linguist / .ts / .qm toolchain: the whole UI is ~90 short strings,
  so a plain dict keeps the build simple (nothing extra to bundle) and lets
  the language switch take effect immediately at runtime.
* `set_language()` mutates module state; widgets re-read strings via
  `tr()` when rebuilt or refreshed. Dialogs are rebuilt on open, so they
  always pick up the current language.

Usage
-----
    from GUI.i18n import tr, set_language, get_language, Language

    label.setText(tr("menu.show"))
    set_language(Language.ZH_CN)
"""

from enum import Enum

# Persisted in QSettings under this key.
LANGUAGE_SETTING_KEY = "app/language"


class Language(Enum):
    EN = "en"
    ZH_CN = "zh_CN"


# Human-readable names shown in the language menu, in the language itself
# (a Chinese speaker looks for "中文", an English speaker for "English").
LANGUAGE_NAMES = {
    Language.EN: "English",
    Language.ZH_CN: "中文",
}

DEFAULT_LANGUAGE = Language.EN

_current = DEFAULT_LANGUAGE


def get_language() -> Language:
    return _current


def set_language(lang) -> None:
    """Set the active language. Accepts a Language or a raw value string."""
    global _current
    if isinstance(lang, Language):
        _current = lang
        return
    if lang is not None:
        for candidate in Language:
            if candidate.value == str(lang):
                _current = candidate
                return
    _current = DEFAULT_LANGUAGE


def language_from_setting(value) -> Language:
    """Parse a persisted QSettings value into a Language."""
    if value is not None:
        for candidate in Language:
            if candidate.value == str(value):
                return candidate
    return DEFAULT_LANGUAGE


def tr(key: str, **kwargs) -> str:
    """Translate `key` into the active language.

    Falls back to English, then to the key itself, so a typo is visible but
    never crashes the UI. Extra kwargs are str.format()'d in.
    """
    text = _TRANSLATIONS.get(_current, {}).get(key)
    if text is None:
        text = _EN.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # A placeholder mismatch must not take down the app; show the
            # unformatted string so the bug is still visible.
            return text
    return text


# ---------------------------------------------------------------------------
# English (source of truth)
# ---------------------------------------------------------------------------
_EN = {
    # --- App / window ---
    "app.name": "Thermal Control Center for Dell G15",

    # --- Tray menu ---
    "menu.mode": "Mode",
    "menu.settings": "Settings",
    "menu.show": "Show",
    "menu.enable_autorun": "Enable autorun",
    "menu.disable_autorun": "Disable autorun",
    "menu.restore_default": "Restore Default",
    "menu.web_server_disabled": "  Web Server: Disabled",
    "menu.web_server_running": "• Web Server: {port}",
    "menu.webhook_enabled": "• Webhook: Enabled",
    "menu.webhook_disabled": "  Webhook: Disabled",
    "menu.exit": "Exit",
    "menu.language": "Language",

    # --- Tray tooltip ---
    "tray.gpu_line": "GPU:    {temp} °C    {rpm} RPM",
    "tray.cpu_line": "CPU:    {temp} °C    {rpm} RPM",
    "tray.mode_line": "Mode:    {mode}",
    "tray.web_line": "Web: http://{ip}:{port}",

    # --- Main window ---
    "main.failsafe": "Fail-safe",
    "main.failsafe_tooltip": (
        "Switch to G-mode (fans on max) when GPU temp reaches "
        "{gpu}°C or CPU reaches {cpu}°C"
    ),
    "main.threshold_gpu": "Threshold GPU temp",
    "main.threshold_cpu": "Threshold CPU temp",
    "main.normal": "Normal",
    "main.last_high_temp": "Last high temp at {time}",

    # --- Dialogs / alerts ---
    "dlg.about": "About",
    "dlg.error": "Error",
    "dlg.success": "Success",
    "dlg.autorun_failed": "Failed to {action} autorun task. Error={err}",
    "dlg.autorun_enabled": "Autorun on system startup Enabled",
    "dlg.autorun_disabled": "Autorun on system startup Disabled",
    "dlg.already_running": "Another instance of this app is already running",

    # --- Thermal mode names ---
    "mode.Balanced": "Balanced",
    "mode.G_Mode": "G-Mode",
    "mode.Custom": "Custom",

    # --- Toast notifications ---
    "toast.mode_changed": "Thermal mode changed",

    # --- Thermal unit widget ---
    "widget.fan_speed": "Fan Speed",
    "widget.copy_hint": "Triple left-click and Ctrl+C to copy",

    # --- Webhook dialog ---
    "wh.title": "Webhook Alert Settings",
    "wh.desc": (
        "Send HTTP notifications when fan speed exceeds threshold.\n"
        "Configure when to trigger and how often to alert."
    ),
    "wh.enable": "Enable Webhook Alert",
    "wh.url": "Webhook URL:",
    "wh.mode_filter": "Mode Filter (only alert in selected modes)",
    "wh.thresholds": "Threshold Settings",
    "wh.gpu_threshold": "GPU RPM Threshold:",
    "wh.cpu_threshold": "CPU RPM Threshold:",
    "wh.behavior": "Alert Behavior",
    "wh.sensitivity": "Sensitivity:",
    "wh.sensitivity_tip": (
        "Low = less sensitive, fewer false alarms\n"
        "Medium = balanced\n"
        "High = more sensitive, faster detection"
    ),
    "wh.frequency": "Alert Frequency:",
    "wh.frequency_tip": "How often to send alerts when threshold is exceeded",
    "wh.body": "Request Body (JSON Template)",
    "wh.template": "Template:",
    "wh.variables": (
        "Variables: {alert_message}, {gpu_rpm}, {cpu_rpm}, {gpu_temp}, "
        "{cpu_temp}, {gpu_rpm_threshold}, {cpu_rpm_threshold}"
    ),
    "wh.status": "Status",
    "wh.last_triggered": "Last Triggered:",
    "wh.alert_count": "Alert Count:",
    "wh.never": "Never",
    "wh.test_send": "Test Send",
    "wh.test_send_tip": "Send a test webhook to verify your configuration",
    "wh.sending": "Sending...",
    "wh.test_ok_title": "Test Successful",
    "wh.test_ok_body": "Webhook sent successfully!\nHTTP Status: {status}",
    "wh.test_fail_title": "Test Failed",
    "wh.test_fail_body": "Failed to send webhook:\n{error}",
    "wh.need_url": "Please enter a Webhook URL first.",
    "wh.need_body": "Please enter a Request Body first.",
    "wh.bad_json": "Invalid JSON body:\n{error}",
    "wh.save": "Save",
    "wh.cancel": "Cancel",

    # Sensitivity / frequency preset display names
    "wh.preset.low": "Low",
    "wh.preset.medium": "Medium",
    "wh.preset.high": "High",
    "wh.preset.immediate": "Immediate (30s max 2min)",
    "wh.preset.moderate": "Moderate (1min max 5min)",
    "wh.preset.conservative": "Conservative (2min max 10min)",

    # Dynamic hint lines
    "wh.hint.detect_low": "Averages {w} readings, ignores brief spikes",
    "wh.hint.detect_medium": "Averages {w} readings, balanced response",
    "wh.hint.detect_high": "Averages {w} readings, reacts quickly to changes",
    "wh.hint.freq": (
        "First alert after {base}s, then every {b2}s, {b4}s... up to {max}s"
    ),
    "wh.hint.combined": "Detection: {detect}\nAlerts: {alerts}",

    # Webhook body templates
    "wh.tpl.custom": "Custom",
    "wh.tpl.default": "Default",
    "wh.tpl.wecom": "WeChat Work (企业微信)",
    "wh.tpl.feishu": "Feishu (飞书)",
    "wh.tpl.dingtalk": "DingTalk (钉钉)",
    "wh.tpl.slack": "Slack",
    "wh.tpl.discord": "Discord",
    "wh.tpl.alert_prefix": "[TCC-G15] Fan speed alert",
    "wh.tpl.gpu_temp_label": "GPU temp",
    "wh.tpl.cpu_temp_label": "CPU temp",

    # --- Web server dialog ---
    "ws.title": "Web Server Settings",
    "ws.desc": (
        "Serve a monitoring dashboard over HTTP so you can watch\n"
        "temperatures and control fans from another device."
    ),
    "ws.enable": "Enable Web Server",
    "ws.network": "Network Settings",
    "ws.port": "Port:",
    "ws.bind": "Bind Address:",
    "ws.bind_tip": "0.0.0.0 = all interfaces, or specific IP like 192.168.1.100",
    "ws.auth_group": "Authentication",
    "ws.auth": "Require Authentication",
    "ws.user": "Username:",
    "ws.pass": "Password:",
    "ws.save": "Save",
    "ws.cancel": "Cancel",
    "ws.security_note": (
        "Do not expose this port directly to the internet. "
        "Use a VPN (e.g. Tailscale) for remote access."
    ),

    # --- Web dashboard (served HTML) ---
    "web.title": "TCC-G15 Web Monitor",
    "web.dashboard": "Dashboard",
    "web.processes": "Processes",
    "web.gpu": "GPU",
    "web.cpu": "CPU",
    "web.temp_history": "Temperature History",
    "web.mode": "Thermal Mode",
    "web.fan_control": "Fan Control",
    "web.apply": "Apply",
    "web.pid": "PID",
    "web.name": "Name",
    "web.cpu_pct": "CPU %",
    "web.memory": "Memory",
    "web.threads": "Threads",
    "web.status": "Status",
    "web.rpm": "RPM",
    "web.temp": "Temp",
    "web.language": "Language",
}

# ---------------------------------------------------------------------------
# Simplified Chinese
# ---------------------------------------------------------------------------
_ZH = {
    "app.name": "Dell G15 温度控制中心",

    "menu.mode": "散热模式",
    "menu.settings": "设置",
    "menu.show": "显示主界面",
    "menu.enable_autorun": "启用开机自启",
    "menu.disable_autorun": "禁用开机自启",
    "menu.restore_default": "恢复默认设置",
    "menu.web_server_disabled": "  Web 服务：未启用",
    "menu.web_server_running": "• Web 服务：{port}",
    "menu.webhook_enabled": "• Webhook：已启用",
    "menu.webhook_disabled": "  Webhook：未启用",
    "menu.exit": "退出",
    "menu.language": "语言",

    "tray.gpu_line": "GPU:    {temp} °C    {rpm} RPM",
    "tray.cpu_line": "CPU:    {temp} °C    {rpm} RPM",
    "tray.mode_line": "模式：    {mode}",
    "tray.web_line": "Web: http://{ip}:{port}",

    "main.failsafe": "过热保护",
    "main.failsafe_tooltip": "当 GPU 温度达到 {gpu}°C 或 CPU 达到 {cpu}°C 时，自动切换到 G-模式（风扇全速）",
    "main.threshold_gpu": "GPU 温度阈值",
    "main.threshold_cpu": "CPU 温度阈值",
    "main.normal": "正常",
    "main.last_high_temp": "上次高温时间：{time}",

    "dlg.about": "关于",
    "dlg.error": "错误",
    "dlg.success": "成功",
    "dlg.autorun_failed": "{action}开机自启任务失败。错误码={err}",
    "dlg.autorun_enabled": "已启用开机自启",
    "dlg.autorun_disabled": "已禁用开机自启",
    "dlg.already_running": "程序已在运行",

    "mode.Balanced": "均衡模式",
    "mode.G_Mode": "G-模式",
    "mode.Custom": "自定义",

    "toast.mode_changed": "散热模式已切换",

    "widget.fan_speed": "风扇转速",
    "widget.copy_hint": "三击左键并 Ctrl+C 可复制",

    "wh.title": "Webhook 告警设置",
    "wh.desc": "当风扇转速超过阈值时发送 HTTP 通知。\n可配置触发条件与告警频率。",
    "wh.enable": "启用 Webhook 告警",
    "wh.url": "Webhook 地址：",
    "wh.mode_filter": "模式过滤（仅在勾选的模式下告警）",
    "wh.thresholds": "阈值设置",
    "wh.gpu_threshold": "GPU 转速阈值：",
    "wh.cpu_threshold": "CPU 转速阈值：",
    "wh.behavior": "告警行为",
    "wh.sensitivity": "灵敏度：",
    "wh.sensitivity_tip": "低 = 更不敏感，误报更少\n中 = 均衡\n高 = 更敏感，检测更快",
    "wh.frequency": "告警频率：",
    "wh.frequency_tip": "超过阈值后发送告警的频率",
    "wh.body": "请求体（JSON 模板）",
    "wh.template": "模板：",
    "wh.variables": (
        "可用变量：{alert_message}、{gpu_rpm}、{cpu_rpm}、{gpu_temp}、"
        "{cpu_temp}、{gpu_rpm_threshold}、{cpu_rpm_threshold}"
    ),
    "wh.status": "状态",
    "wh.last_triggered": "上次触发：",
    "wh.alert_count": "告警次数：",
    "wh.never": "从未",
    "wh.test_send": "发送测试",
    "wh.test_send_tip": "发送一条测试 Webhook 以验证配置",
    "wh.sending": "发送中...",
    "wh.test_ok_title": "测试成功",
    "wh.test_ok_body": "Webhook 发送成功！\nHTTP 状态码：{status}",
    "wh.test_fail_title": "测试失败",
    "wh.test_fail_body": "Webhook 发送失败：\n{error}",
    "wh.need_url": "请先填写 Webhook 地址。",
    "wh.need_body": "请先填写请求体。",
    "wh.bad_json": "JSON 格式错误：\n{error}",
    "wh.save": "保存",
    "wh.cancel": "取消",

    "wh.preset.low": "低",
    "wh.preset.medium": "中",
    "wh.preset.high": "高",
    "wh.preset.immediate": "立即（30秒，最长2分钟）",
    "wh.preset.moderate": "适中（1分钟，最长5分钟）",
    "wh.preset.conservative": "保守（2分钟，最长10分钟）",

    "wh.hint.detect_low": "对 {w} 次采样取平均，过滤瞬时尖峰",
    "wh.hint.detect_medium": "对 {w} 次采样取平均，响应均衡",
    "wh.hint.detect_high": "对 {w} 次采样取平均，快速响应变化",
    "wh.hint.freq": "首次告警在 {base} 秒后，之后按 {b2}s、{b4}s 递增，最长 {max}s",
    "wh.hint.combined": "检测：{detect}\n告警：{alerts}",

    "wh.tpl.custom": "自定义",
    "wh.tpl.default": "默认",
    "wh.tpl.wecom": "企业微信",
    "wh.tpl.feishu": "飞书",
    "wh.tpl.dingtalk": "钉钉",
    "wh.tpl.slack": "Slack",
    "wh.tpl.discord": "Discord",
    "wh.tpl.alert_prefix": "[TCC-G15] 风扇速度告警",
    "wh.tpl.gpu_temp_label": "GPU温度",
    "wh.tpl.cpu_temp_label": "CPU温度",

    "ws.title": "Web 服务设置",
    "ws.desc": "通过 HTTP 提供监控面板，可在其他设备上查看温度并控制风扇。",
    "ws.enable": "启用 Web 服务",
    "ws.network": "网络设置",
    "ws.port": "端口：",
    "ws.bind": "绑定地址：",
    "ws.bind_tip": "0.0.0.0 = 监听所有网卡；也可填指定 IP，如 192.168.1.100",
    "ws.auth_group": "身份验证",
    "ws.auth": "启用身份验证",
    "ws.user": "用户名：",
    "ws.pass": "密码：",
    "ws.save": "保存",
    "ws.cancel": "取消",
    "ws.security_note": "请勿将此端口直接暴露到公网。远程访问建议使用 VPN（如 Tailscale）。",

    "web.title": "TCC-G15 网页监控",
    "web.dashboard": "仪表盘",
    "web.processes": "进程",
    "web.gpu": "GPU",
    "web.cpu": "CPU",
    "web.temp_history": "温度历史",
    "web.mode": "散热模式",
    "web.fan_control": "风扇控制",
    "web.apply": "应用",
    "web.pid": "PID",
    "web.name": "名称",
    "web.cpu_pct": "CPU %",
    "web.memory": "内存",
    "web.threads": "线程",
    "web.status": "状态",
    "web.rpm": "转速",
    "web.temp": "温度",
    "web.language": "语言",
}

_TRANSLATIONS = {
    Language.EN: _EN,
    Language.ZH_CN: _ZH,
}

# All keys, useful for a completeness check in tests.
KEYS = set(_EN)
