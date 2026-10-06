import json
import datetime
import urllib.request
import threading

from typing import Optional
from PySide6 import QtCore, QtWidgets

from GUI.Settings import SettingsKey, WEBHOOK_DEFAULTS, WEB_DEFAULTS, setting_bool, setting_str, setting_int, setting_float
from GUI.i18n import tr


def _replace_webhook_variables(template: str, values: dict) -> str:
    """Replace template variables in a webhook body string.

    Supported placeholders: {alert_message}, {gpu_rpm}, {cpu_rpm},
    {gpu_temp}, {cpu_temp}, {gpu_threshold}, {cpu_threshold}.
    """
    result = template
    for key, val in values.items():
        if isinstance(val, str):
            escaped = json.dumps(val)[1:-1]
        else:
            escaped = str(val)
        result = result.replace('{' + key + '}', escaped)
    return result


class WebhookDialog(QtWidgets.QDialog):
    # 信号定义
    testComplete = QtCore.Signal(int, str)

    # Sensitivity presets keyed by a STABLE id (not the display text, which
    # changes with the language): id -> (window_size, sigma)
    SENSITIVITY_IDS = {
        "low": (8, 2.5),
        "medium": (5, 2.0),
        "high": (3, 1.5),
    }
    SENSITIVITY_LABEL_KEYS = {
        "low": "wh.preset.low",
        "medium": "wh.preset.medium",
        "high": "wh.preset.high",
    }
    SENSITIVITY_HINT_KEYS = {
        "low": "wh.hint.detect_low",
        "medium": "wh.hint.detect_medium",
        "high": "wh.hint.detect_high",
    }

    # Frequency presets: id -> (base_interval, max_interval)
    FREQUENCY_IDS = {
        "immediate": (30, 120),
        "moderate": (60, 300),
        "conservative": (120, 600),
    }
    FREQUENCY_LABEL_KEYS = {
        "immediate": "wh.preset.immediate",
        "moderate": "wh.preset.moderate",
        "conservative": "wh.preset.conservative",
    }

    def __init__(self, parent, settings, webhook_status=None):
        super().__init__(parent)
        self.settings = settings
        self.webhook_status = webhook_status or {}
        self.setWindowTitle(tr("wh.title"))
        self.setMinimumWidth(500)

        layout = QtWidgets.QVBoxLayout(self)

        # 功能说明
        descLabel = QtWidgets.QLabel(tr("wh.desc"))
        descLabel.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        descLabel.setWordWrap(True)
        layout.addWidget(descLabel)

        # 启用开关
        self.enableCB = QtWidgets.QCheckBox(tr("wh.enable"))
        layout.addWidget(self.enableCB)

        # URL 输入
        urlLayout = QtWidgets.QHBoxLayout()
        urlLayout.addWidget(QtWidgets.QLabel(tr("wh.url")))
        self.urlEdit = QtWidgets.QLineEdit()
        self.urlEdit.setPlaceholderText("https://your-webhook-url.com/endpoint")
        urlLayout.addWidget(self.urlEdit)
        layout.addLayout(urlLayout)

        # 模式过滤
        filterGroup = QtWidgets.QGroupBox(tr("wh.mode_filter"))
        filterLayout = QtWidgets.QHBoxLayout(filterGroup)
        self.filterBalancedCB = QtWidgets.QCheckBox(tr("mode.Balanced"))
        self.filterGModeCB = QtWidgets.QCheckBox(tr("mode.G_Mode"))
        self.filterCustomCB = QtWidgets.QCheckBox(tr("mode.Custom"))
        self.filterBalancedCB.setChecked(True)
        self.filterGModeCB.setChecked(True)
        self.filterCustomCB.setChecked(True)
        filterLayout.addWidget(self.filterBalancedCB)
        filterLayout.addWidget(self.filterGModeCB)
        filterLayout.addWidget(self.filterCustomCB)
        layout.addWidget(filterGroup)

        # 阈值设置
        thresholdGroup = QtWidgets.QGroupBox(tr("wh.thresholds"))
        thresholdLayout = QtWidgets.QFormLayout(thresholdGroup)

        self.gpuThresholdSpin = QtWidgets.QSpinBox()
        self.gpuThresholdSpin.setRange(1000, 6000)
        self.gpuThresholdSpin.setSuffix(" RPM")
        thresholdLayout.addRow(tr("wh.gpu_threshold"), self.gpuThresholdSpin)

        self.cpuThresholdSpin = QtWidgets.QSpinBox()
        self.cpuThresholdSpin.setRange(1000, 6000)
        self.cpuThresholdSpin.setSuffix(" RPM")
        thresholdLayout.addRow(tr("wh.cpu_threshold"), self.cpuThresholdSpin)

        layout.addWidget(thresholdGroup)

        # 行为设置（人性化选项）
        behaviorGroup = QtWidgets.QGroupBox(tr("wh.behavior"))
        behaviorLayout = QtWidgets.QFormLayout(behaviorGroup)

        self.sensitivityCombo = QtWidgets.QComboBox()
        for sid in self.SENSITIVITY_IDS:
            self.sensitivityCombo.addItem(tr(self.SENSITIVITY_LABEL_KEYS[sid]), sid)
        self.sensitivityCombo.setToolTip(tr("wh.sensitivity_tip"))
        behaviorLayout.addRow(tr("wh.sensitivity"), self.sensitivityCombo)

        self.frequencyCombo = QtWidgets.QComboBox()
        for fid in self.FREQUENCY_IDS:
            self.frequencyCombo.addItem(tr(self.FREQUENCY_LABEL_KEYS[fid]), fid)
        self.frequencyCombo.setToolTip(tr("wh.frequency_tip"))
        behaviorLayout.addRow(tr("wh.frequency"), self.frequencyCombo)

        # 动态说明标签
        self.behaviorHint = QtWidgets.QLabel()
        self.behaviorHint.setStyleSheet("color: grey; font-size: 10px;")
        self.behaviorHint.setWordWrap(True)
        behaviorLayout.addRow(self.behaviorHint)

        def updateBehaviorHint():
            sid = self.sensitivityCombo.currentData()
            fid = self.frequencyCombo.currentData()
            ws, _ = self.SENSITIVITY_IDS[sid]
            sDesc = tr(self.SENSITIVITY_HINT_KEYS[sid], w=ws)
            base, max_ = self.FREQUENCY_IDS[fid]
            fDesc = tr("wh.hint.freq", base=base, b2=base * 2, b4=base * 4, max=max_)
            self.behaviorHint.setText(tr("wh.hint.combined", detect=sDesc, alerts=fDesc))

        self.sensitivityCombo.currentIndexChanged.connect(updateBehaviorHint)
        self.frequencyCombo.currentIndexChanged.connect(updateBehaviorHint)
        updateBehaviorHint()

        layout.addWidget(behaviorGroup)

        # Body 模板
        bodyGroup = QtWidgets.QGroupBox(tr("wh.body"))
        bodyLayout = QtWidgets.QVBoxLayout(bodyGroup)

        # 模板选择
        templateLayout = QtWidgets.QHBoxLayout()
        templateLayout.addWidget(QtWidgets.QLabel(tr("wh.template")))
        self.templateCombo = QtWidgets.QComboBox()
        # item data carries a stable template id; the text is translated.
        for tid, key in (
            ("custom", "wh.tpl.custom"),
            ("default", "wh.tpl.default"),
            ("wecom", "wh.tpl.wecom"),
            ("feishu", "wh.tpl.feishu"),
            ("dingtalk", "wh.tpl.dingtalk"),
            ("slack", "wh.tpl.slack"),
            ("discord", "wh.tpl.discord"),
        ):
            self.templateCombo.addItem(tr(key), tid)
        templateLayout.addWidget(self.templateCombo)
        templateLayout.addStretch()
        bodyLayout.addLayout(templateLayout)

        self.bodyEdit = QtWidgets.QPlainTextEdit()
        self.bodyEdit.setMaximumHeight(150)
        self.bodyEdit.setPlaceholderText('{"text": "Alert: {alert_message}", "gpu_rpm": {gpu_rpm}}')
        bodyLayout.addWidget(self.bodyEdit)

        # 模板定义（URL 示例 + Body），键为稳定的模板 id
        def _alert_text(prefix_key: str, gpu_label_key: str, cpu_label_key: str) -> str:
            return tr(prefix_key) + "\\n{alert_message}\\nGPU: {gpu_rpm} RPM, CPU: {cpu_rpm} RPM\\n" \
                + tr(gpu_label_key) + ": {gpu_temp}°C, " + tr(cpu_label_key) + ": {cpu_temp}°C"

        self._templates = {
            "default": {
                "url": "https://your-webhook-url.com/endpoint",
                "body": WEBHOOK_DEFAULTS["body"],
            },
            "wecom": {
                "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY",
                "body": '{"msgtype": "text", "text": {"content": "' + _alert_text("wh.tpl.alert_prefix", "wh.tpl.gpu_temp_label", "wh.tpl.cpu_temp_label") + '"}}',
            },
            "feishu": {
                "url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK",
                "body": '{"msg_type": "text", "content": {"text": "' + _alert_text("wh.tpl.alert_prefix", "wh.tpl.gpu_temp_label", "wh.tpl.cpu_temp_label") + '"}}',
            },
            "dingtalk": {
                "url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
                "body": '{"msgtype": "text", "text": {"content": "' + _alert_text("wh.tpl.alert_prefix", "wh.tpl.gpu_temp_label", "wh.tpl.cpu_temp_label") + '"}}',
            },
            "slack": {
                "url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
                "body": '{"text": "' + _alert_text("wh.tpl.alert_prefix", "wh.tpl.gpu_temp_label", "wh.tpl.cpu_temp_label") + '"}',
            },
            "discord": {
                "url": "https://discord.com/api/webhooks/YOUR/WEBHOOK",
                "body": '{"content": "' + _alert_text("wh.tpl.alert_prefix", "wh.tpl.gpu_temp_label", "wh.tpl.cpu_temp_label") + '"}',
            },
        }

        def onTemplateChange():
            template = self.templateCombo.currentData()
            if template in self._templates:
                self.bodyEdit.setPlainText(self._templates[template]["body"])
                self.urlEdit.setText(self._templates[template]["url"])

        self.templateCombo.currentIndexChanged.connect(onTemplateChange)

        helpLabel = QtWidgets.QLabel(tr("wh.variables"))
        helpLabel.setStyleSheet("color: grey; font-size: 10px;")
        helpLabel.setWordWrap(True)
        bodyLayout.addWidget(helpLabel)

        layout.addWidget(bodyGroup)

        # 状态信息
        statusGroup = QtWidgets.QGroupBox(tr("wh.status"))
        statusLayout = QtWidgets.QFormLayout(statusGroup)

        self.lastTriggerLabel = QtWidgets.QLabel(tr("wh.never"))
        statusLayout.addRow(tr("wh.last_triggered"), self.lastTriggerLabel)

        self.alertCountLabel = QtWidgets.QLabel("0")
        statusLayout.addRow(tr("wh.alert_count"), self.alertCountLabel)

        layout.addWidget(statusGroup)

        # 按钮行
        buttonLayout = QtWidgets.QHBoxLayout()

        self.testBtn = QtWidgets.QPushButton(tr("wh.test_send"))
        self.testBtn.setToolTip(tr("wh.test_send_tip"))
        self.testBtn.clicked.connect(self._testSend)
        self.testComplete.connect(self._onTestComplete)
        buttonLayout.addWidget(self.testBtn)

        buttonLayout.addStretch()

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttonBox.button(QtWidgets.QDialogButtonBox.Ok).setText(tr("wh.save"))
        buttonBox.button(QtWidgets.QDialogButtonBox.Cancel).setText(tr("wh.cancel"))
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        buttonLayout.addWidget(buttonBox)

        layout.addLayout(buttonLayout)

        # 加载当前设置
        self._loadSettings()

    def _onTestComplete(self, status, error):
        self.testBtn.setEnabled(True)
        self.testBtn.setText(tr("wh.test_send"))
        if status:
            QtWidgets.QMessageBox.information(self, tr("wh.test_ok_title"), tr("wh.test_ok_body", status=status))
        else:
            QtWidgets.QMessageBox.warning(self, tr("wh.test_fail_title"), tr("wh.test_fail_body", error=error))

    def _testSend(self):
        """发送测试 webhook"""
        url = self.urlEdit.text().strip()
        body = self.bodyEdit.toPlainText().strip()

        if not url:
            QtWidgets.QMessageBox.warning(self, tr("wh.test_fail_title"), tr("wh.need_url"))
            return

        if not body:
            QtWidgets.QMessageBox.warning(self, tr("wh.test_fail_title"), tr("wh.need_body"))
            return

        # 替换变量为测试值
        testBody = _replace_webhook_variables(body, {
            'alert_message': 'Test alert from TCC-G15',
            'gpu_rpm': 3500,
            'cpu_rpm': 4200,
            'gpu_temp': 72,
            'cpu_temp': 85,
            'gpu_rpm_threshold': 4000,
            'cpu_rpm_threshold': 4000,
        })

        try:
            json.loads(testBody)
        except json.JSONDecodeError as e:
            QtWidgets.QMessageBox.warning(self, tr("wh.test_fail_title"), tr("wh.bad_json", error=e))
            return

        def _doRequest():
            try:
                payload = testBody.encode("utf-8")
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, None
            except Exception as e:
                return None, str(e)

        # 在后台线程执行
        self.testBtn.setEnabled(False)
        self.testBtn.setText(tr("wh.sending"))

        def _doTest():
            status, error = _doRequest()
            self.testComplete.emit(status if status else 0, error if error else "")

        thread = threading.Thread(target=_doTest, daemon=True)
        thread.start()

    def _loadSettings(self):
        self.enableCB.setChecked(setting_bool(self.settings, SettingsKey.WebhookEnabled.value, WEBHOOK_DEFAULTS["enabled"]))
        self.urlEdit.setText(setting_str(self.settings, SettingsKey.WebhookUrl.value, WEBHOOK_DEFAULTS["url"]))
        savedBody = setting_str(self.settings, SettingsKey.WebhookBody.value, WEBHOOK_DEFAULTS["body"])
        self.bodyEdit.setPlainText(savedBody)
        self.gpuThresholdSpin.setValue(setting_int(self.settings, SettingsKey.WebhookGpuRpmThreshold.value, WEBHOOK_DEFAULTS["gpu_rpm_threshold"]))
        self.cpuThresholdSpin.setValue(setting_int(self.settings, SettingsKey.WebhookCpuRpmThreshold.value, WEBHOOK_DEFAULTS["cpu_rpm_threshold"]))

        # 加载模式过滤设置
        self.filterBalancedCB.setChecked(setting_bool(self.settings, SettingsKey.WebhookFilterBalanced.value, WEBHOOK_DEFAULTS["filter_balanced"]))
        self.filterGModeCB.setChecked(setting_bool(self.settings, SettingsKey.WebhookFilterGMode.value, WEBHOOK_DEFAULTS["filter_gmode"]))
        self.filterCustomCB.setChecked(setting_bool(self.settings, SettingsKey.WebhookFilterCustom.value, WEBHOOK_DEFAULTS["filter_custom"]))

        # 匹配模板
        matchedTemplate = "custom"
        for tid, tmpl in self._templates.items():
            if savedBody.strip() == tmpl["body"].strip():
                matchedTemplate = tid
                break
        self.templateCombo.blockSignals(True)
        idx = self.templateCombo.findData(matchedTemplate)
        self.templateCombo.setCurrentIndex(idx if idx >= 0 else 0)
        self.templateCombo.blockSignals(False)

        # 加载灵敏度预设
        savedSigma = setting_float(self.settings, SettingsKey.WebhookSigma.value, WEBHOOK_DEFAULTS["sigma"])
        sensitivity = "medium"
        for sid, (_, sigma) in self.SENSITIVITY_IDS.items():
            if abs(sigma - savedSigma) < 0.01:
                sensitivity = sid
                break
        sIdx = self.sensitivityCombo.findData(sensitivity)
        if sIdx >= 0:
            self.sensitivityCombo.setCurrentIndex(sIdx)

        # 加载频率预设
        savedBase = setting_int(self.settings, SettingsKey.WebhookBaseInterval.value, WEBHOOK_DEFAULTS["base_interval"])
        savedMax = setting_int(self.settings, SettingsKey.WebhookMaxInterval.value, WEBHOOK_DEFAULTS["max_interval"])
        frequency = "immediate"
        for fid, (base, max_) in self.FREQUENCY_IDS.items():
            if base == savedBase and max_ == savedMax:
                frequency = fid
                break
        fIdx = self.frequencyCombo.findData(frequency)
        if fIdx >= 0:
            self.frequencyCombo.setCurrentIndex(fIdx)

        # 加载状态信息
        lastTrigger = self.webhook_status.get('last_trigger_time', 0)
        alertCount = self.webhook_status.get('alert_count', 0)
        if lastTrigger > 0:
            triggerTime = datetime.datetime.fromtimestamp(lastTrigger).strftime("%Y-%m-%d %H:%M:%S")
            self.lastTriggerLabel.setText(triggerTime)
        else:
            self.lastTriggerLabel.setText(tr("wh.never"))
        self.alertCountLabel.setText(str(alertCount))

    def getSettings(self):
        window_size, sigma = self.SENSITIVITY_IDS[self.sensitivityCombo.currentData()]
        base_interval, max_interval = self.FREQUENCY_IDS[self.frequencyCombo.currentData()]
        return {
            'enabled': self.enableCB.isChecked(),
            'url': self.urlEdit.text().strip(),
            'body': self.bodyEdit.toPlainText().strip(),
            'gpu_rpm_threshold': self.gpuThresholdSpin.value(),
            'cpu_rpm_threshold': self.cpuThresholdSpin.value(),
            'window_size': window_size,
            'sigma': sigma,
            'base_interval': base_interval,
            'max_interval': max_interval,
            'cooldown': setting_int(self.settings, SettingsKey.WebhookCooldownAfterReset.value, WEBHOOK_DEFAULTS["cooldown_after_reset"]),
            'filter_balanced': self.filterBalancedCB.isChecked(),
            'filter_gmode': self.filterGModeCB.isChecked(),
            'filter_custom': self.filterCustomCB.isChecked(),
        }


class WebServerDialog(QtWidgets.QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr("ws.title"))
        self.setMinimumWidth(450)

        layout = QtWidgets.QVBoxLayout(self)

        # 功能说明
        descLabel = QtWidgets.QLabel(tr("ws.desc"))
        descLabel.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        descLabel.setWordWrap(True)
        layout.addWidget(descLabel)

        # 启用开关
        self.enableCB = QtWidgets.QCheckBox(tr("ws.enable"))
        layout.addWidget(self.enableCB)

        # 网络设置
        networkGroup = QtWidgets.QGroupBox(tr("ws.network"))
        networkLayout = QtWidgets.QFormLayout(networkGroup)

        self.portSpin = QtWidgets.QSpinBox()
        self.portSpin.setRange(1024, 65535)
        networkLayout.addRow(tr("ws.port"), self.portSpin)

        self.bindEdit = QtWidgets.QLineEdit()
        self.bindEdit.setPlaceholderText("0.0.0.0")
        self.bindEdit.setToolTip(tr("ws.bind_tip"))
        networkLayout.addRow(tr("ws.bind"), self.bindEdit)

        layout.addWidget(networkGroup)

        # 认证设置
        authGroup = QtWidgets.QGroupBox(tr("ws.auth_group"))
        authLayout = QtWidgets.QFormLayout(authGroup)

        self.authEnableCB = QtWidgets.QCheckBox(tr("ws.auth"))
        authLayout.addRow(self.authEnableCB)

        self.userEdit = QtWidgets.QLineEdit()
        self.userEdit.setPlaceholderText("admin")
        authLayout.addRow(tr("ws.user"), self.userEdit)

        self.passEdit = QtWidgets.QLineEdit()
        self.passEdit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.passEdit.setPlaceholderText("password")
        authLayout.addRow(tr("ws.pass"), self.passEdit)

        layout.addWidget(authGroup)

        # Security reminder — this port controls fans, so a warning is warranted.
        noteLabel = QtWidgets.QLabel(tr("ws.security_note"))
        noteLabel.setStyleSheet("color: #b58900; font-size: 10px;")
        noteLabel.setWordWrap(True)
        layout.addWidget(noteLabel)

        # 按钮
        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttonBox.button(QtWidgets.QDialogButtonBox.Ok).setText(tr("ws.save"))
        buttonBox.button(QtWidgets.QDialogButtonBox.Cancel).setText(tr("ws.cancel"))
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)

        self._loadConfig()

    def _loadConfig(self):
        self.enableCB.setChecked(setting_bool(self.settings, SettingsKey.WebEnabled.value, WEB_DEFAULTS["web_enabled"]))
        self.portSpin.setValue(setting_int(self.settings, SettingsKey.WebPort.value, WEB_DEFAULTS["web_port"]))
        self.bindEdit.setText(setting_str(self.settings, SettingsKey.WebBindAddr.value, WEB_DEFAULTS["bind_addr"]))
        self.authEnableCB.setChecked(setting_bool(self.settings, SettingsKey.WebAuthEnabled.value, WEB_DEFAULTS["auth_enabled"]))
        self.userEdit.setText(setting_str(self.settings, SettingsKey.WebAuthUser.value, WEB_DEFAULTS["auth_user"]))
        self.passEdit.setText(setting_str(self.settings, SettingsKey.WebAuthPass.value, WEB_DEFAULTS["auth_pass"]))

    def getConfig(self):
        return {
            "web_enabled": self.enableCB.isChecked(),
            "web_port": self.portSpin.value(),
            "bind_addr": self.bindEdit.text().strip() or "0.0.0.0",
            "auth_enabled": self.authEnableCB.isChecked(),
            "auth_user": self.userEdit.text().strip() or "admin",
            "auth_pass": self.passEdit.text(),
        }
