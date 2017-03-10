import locale
import random
import time

import psutil
from PyQt5.QtCore import Qt, pyqtSlot, QTimer, QRegExp, pyqtSignal
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtGui import QRegExpValidator, QIcon
from PyQt5.QtGui import QTransform
from PyQt5.QtWidgets import QDialog, QApplication
from PyQt5.QtWidgets import QGraphicsView
from PyQt5.QtWidgets import QSlider
from PyQt5.QtWidgets import QSpinBox

from urh import constants
from urh.dev import config
from urh.dev.BackendHandler import BackendHandler, Backends
from urh.dev.VirtualDevice import VirtualDevice
from urh.plugins.NetworkSDRInterface.NetworkSDRInterfacePlugin import NetworkSDRInterfacePlugin
from urh.plugins.PluginManager import PluginManager
from urh.ui.ui_send_recv import Ui_SendRecvDialog
from urh.util.Errors import Errors
from urh.util.Logger import logger
from urh.util.ProjectManager import ProjectManager


class SendRecvDialogController(QDialog):
    recording_parameters = pyqtSignal(str, dict)

    def __init__(self, project_manager: ProjectManager, is_tx: bool, parent=None, testing_mode=False):
        super().__init__(parent)
        self.is_tx = is_tx

        self.ui = Ui_SendRecvDialog()
        self.ui.setupUi(self)

        self.set_sniff_ui_items_visible(False)

        self.graphics_view = None  # type: QGraphicsView
        self.__device = None  # type: VirtualDevice

        self.backend_handler = BackendHandler(testing_mode=testing_mode)

        self.ui.btnStop.setEnabled(False)
        self.ui.btnClear.setEnabled(False)
        self.ui.btnSave.setEnabled(False)

        self.start = 0

        self.bw_sr_are_locked = constants.SETTINGS.value("lock_bandwidth_sample_rate", True, bool)

        self.ui.spinBoxFreq.setValue(project_manager.device_conf["frequency"])
        self.ui.spinBoxSampleRate.setValue(project_manager.device_conf["sample_rate"])
        self.ui.spinBoxBandwidth.setValue(project_manager.device_conf["bandwidth"])
        self.ui.spinBoxGain.setValue(project_manager.device_conf["gain"])
        try:
            if_gain = project_manager.device_conf["if_gain"]
        except KeyError:
            if_gain = config.DEFAULT_IF_GAIN
        self.ui.spinBoxIFGain.setValue(if_gain)

        try:
            baseband_gain = project_manager.device_conf["baseband_gain"]
        except KeyError:
            baseband_gain = config.DEFAULT_BB_GAIN
        self.ui.spinBoxBasebandGain.setValue(baseband_gain)

        try:
            freq_correction = project_manager.device_conf["freq_correction"]
        except KeyError:
            freq_correction = config.DEFAULT_FREQ_CORRECTION
        self.ui.spinBoxFreqCorrection.setValue(freq_correction)

        self.ui.spinBoxNRepeat.setValue(constants.SETTINGS.value('num_sending_repeats', 1, type=int))
        device = project_manager.device

        self.ui.cbDevice.clear()
        items = self.get_devices_for_combobox()
        self.ui.cbDevice.addItems(items)

        if device in items:
            self.ui.cbDevice.setCurrentIndex(items.index(device))

        self.timer = QTimer(self)

        dev_name = self.ui.cbDevice.currentText()
        self.set_device_ui_items_visibility(dev_name)

        self.ui.btnLockBWSR.setChecked(self.bw_sr_are_locked)
        self.on_btn_lock_bw_sr_clicked()

    @property
    def is_rx(self) -> bool:
        return not self.is_tx

    @property
    def rx_tx_prefix(self) -> str:
        return "rx_" if self.is_rx else "tx_"

    @property
    def has_empty_device_list(self):
        return self.ui.cbDevice.count() == 0

    @property
    def device(self) -> VirtualDevice:
        return self.__device

    @device.setter
    def device(self, value):
        self.__device = value

    def hide_send_ui_items(self):
        for item in ("spinBoxNRepeat", "labelNRepeat", "lblCurrentRepeatValue",
                     "lblRepeatText", "lSamplesSentText", "progressBar"):
            getattr(self.ui, item).hide()

    def hide_receive_ui_items(self):
        for item in ("lSamplesCaptured", "lSamplesCapturedText", "lSignalSize", "lSignalSizeText",
                     "lTime", "lTimeText", "btnSave", "labelReceiveBufferFull", "lReceiveBufferFullText"):
            getattr(self.ui, item).hide()

    def set_sniff_ui_items_visible(self, visible: bool):
        for item in self.ui.__dict__:
            if "_sniff_" in item:
                getattr(self.ui, item).setVisible(visible)

    def get_config_for_selected_device(self):
        device_name = self.ui.cbDevice.currentText()
        key = device_name if device_name in config.DEVICE_CONFIG.keys() else "Fallback"
        conf = config.DEVICE_CONFIG[key]
        return conf

    def sync_gain_sliders(self):
        conf = self.get_config_for_selected_device()
        prefix = self.rx_tx_prefix

        if prefix + "rf_gain" in conf:
            gain = min(conf[prefix + "rf_gain"], key=lambda x: abs(x - self.ui.spinBoxGain.value()))
            self.ui.spinBoxGain.setValue(gain)
            self.ui.spinBoxGain.valueChanged.emit(gain)
        if prefix + "if_gain" in conf:
            if_gain = min(conf[prefix + "if_gain"], key=lambda x: abs(x - self.ui.spinBoxIFGain.value()))
            self.ui.spinBoxIFGain.setValue(if_gain)
            self.ui.spinBoxIFGain.valueChanged.emit(if_gain)
        if prefix + "baseband_gain" in conf:
            baseband_gain = min(conf[prefix + "baseband_gain"],
                                key=lambda x: abs(x - self.ui.spinBoxBasebandGain.value()))
            self.ui.spinBoxBasebandGain.setValue(baseband_gain)
            self.ui.spinBoxBasebandGain.valueChanged.emit(baseband_gain)

    def set_device_ui_items_visibility(self, device_name: str):
        key = device_name if device_name in config.DEVICE_CONFIG.keys() else "Fallback"
        conf = config.DEVICE_CONFIG[key]
        self.ui.spinBoxFreq.setVisible("center_freq" in conf)
        self.ui.labelFreq.setVisible("center_freq" in conf)
        self.ui.spinBoxSampleRate.setVisible("sample_rate" in conf)
        self.ui.labelSampleRate.setVisible("sample_rate" in conf)
        self.ui.spinBoxBandwidth.setVisible("bandwidth" in conf)
        self.ui.labelBandWidth.setVisible("bandwidth" in conf)
        self.ui.btnLockBWSR.setVisible("sample_rate" in conf and "bandwidth" in conf)

        if "freq_correction" in conf:
            self.ui.labelFreqCorrection.setVisible(True)
            self.ui.spinBoxFreqCorrection.setVisible(True)
            self.ui.spinBoxFreqCorrection.setMinimum(conf["freq_correction"].start)
            self.ui.spinBoxFreqCorrection.setMaximum(conf["freq_correction"].stop)
            self.ui.spinBoxFreqCorrection.setSingleStep(conf["freq_correction"].step)
        else:
            self.ui.labelFreqCorrection.setVisible(False)
            self.ui.spinBoxFreqCorrection.setVisible(False)

        if "direct_sampling" in conf:
            self.ui.labelDirectSampling.setVisible(True)
            self.ui.comboBoxDirectSampling.setVisible(True)
            items = [self.ui.comboBoxDirectSampling.itemText(i) for i in range(self.ui.comboBoxDirectSampling.count())]
            if items != conf["direct_sampling"]:
                self.ui.comboBoxDirectSampling.clear()
                self.ui.comboBoxDirectSampling.addItems(conf["direct_sampling"])
        else:
            self.ui.labelDirectSampling.setVisible(False)
            self.ui.comboBoxDirectSampling.setVisible(False)

        prefix = self.rx_tx_prefix
        key_ui_gain_map = {prefix + "rf_gain": "Gain", prefix + "if_gain": "IFGain",
                           prefix + "baseband_gain": "BasebandGain"}
        for conf_key, ui_element in key_ui_gain_map.items():
            getattr(self.ui, "label" + ui_element).setVisible(conf_key in conf)

            spinbox = getattr(self.ui, "spinBox" + ui_element)  # type: QSpinBox
            slider = getattr(self.ui, "slider" + ui_element)  # type: QSlider

            if conf_key in conf:
                gain_values = conf[conf_key]
                assert len(gain_values) >= 2
                spinbox.setMinimum(gain_values[0])
                spinbox.setMaximum(gain_values[-1])
                spinbox.setSingleStep(gain_values[1] - gain_values[0])
                spinbox.setVisible(True)

                slider.setMaximum(len(gain_values) - 1)
            else:
                spinbox.setVisible(False)
                slider.setVisible(False)
            getattr(self.ui, "slider" + ui_element).setVisible(conf_key in conf)

        self.ui.lineEditIP.setVisible("ip" in conf)
        self.ui.labelIP.setVisible("ip" in conf)
        self.ui.spinBoxPort.setVisible("port" in conf)
        self.ui.labelPort.setVisible("port" in conf)

    def set_device_ui_items_enabled(self, enabled: bool):
        self.ui.spinBoxFreq.setEnabled(enabled)
        self.ui.spinBoxGain.setEnabled(enabled)
        self.ui.sliderGain.setEnabled(enabled)
        self.ui.spinBoxIFGain.setEnabled(enabled)
        self.ui.sliderIFGain.setEnabled(enabled)
        self.ui.spinBoxBasebandGain.setEnabled(enabled)
        self.ui.sliderBasebandGain.setEnabled(enabled)
        self.ui.spinBoxBandwidth.setEnabled(enabled)
        self.ui.spinBoxSampleRate.setEnabled(enabled)
        self.ui.spinBoxFreqCorrection.setEnabled(enabled)
        self.ui.comboBoxDirectSampling.setEnabled(enabled)
        self.ui.lineEditIP.setEnabled(enabled)
        self.ui.spinBoxNRepeat.setEnabled(enabled)
        self.ui.cbDevice.setEnabled(enabled)
        self.ui.spinBoxPort.setEnabled(enabled)

    def emit_editing_finished_signals(self):
        self.ui.spinBoxFreq.editingFinished.emit()
        self.ui.spinBoxBandwidth.editingFinished.emit()
        self.ui.spinBoxGain.editingFinished.emit()
        self.ui.spinBoxIFGain.editingFinished.emit()
        self.ui.spinBoxBasebandGain.editingFinished.emit()
        self.ui.spinBoxNRepeat.editingFinished.emit()
        self.ui.spinBoxSampleRate.editingFinished.emit()
        self.ui.spinBoxFreqCorrection.editingFinished.emit()
        if self.ui.cbDevice.currentText() in ("USRP", "RTL-TCP"):
            self.ui.lineEditIP.editingFinished.emit()
        if self.ui.cbDevice.currentText() == "RTL-TCP":
            self.ui.spinBoxPort.editingFinished.emit()

    def get_devices_for_combobox(self):
        items = []
        for device_name in self.backend_handler.DEVICE_NAMES:
            dev = self.backend_handler.device_backends[device_name.lower()]
            if self.is_tx and dev.is_enabled and dev.supports_tx:
                items.append(device_name)
            elif self.is_rx and dev.is_enabled and dev.supports_rx:
                items.append(device_name)

        if PluginManager().is_plugin_enabled("NetworkSDRInterface"):
            items.append(NetworkSDRInterfacePlugin.NETWORK_SDR_NAME)

        return items

    def set_bandwidth_status(self):
        if self.device is not None:
            self.ui.spinBoxBandwidth.setEnabled(self.device.bandwidth_is_adjustable)
            self.ui.btnLockBWSR.setEnabled(self.device.bandwidth_is_adjustable)

            if not self.device.bandwidth_is_adjustable:
                self.bw_sr_are_locked = False
                self.ui.spinBoxBandwidth.setToolTip(self.tr("Your driver of RTL-SDR does not support "
                                                            "setting the bandwidth. "
                                                            "If you need this feature, install a recent version."))
            else:
                self.ui.spinBoxBandwidth.setToolTip("")
                self.bw_sr_are_locked = self.ui.btnLockBWSR.isChecked()

    def create_connects(self):
        self.ui.btnStart.clicked.connect(self.on_start_clicked)
        self.ui.btnStop.clicked.connect(self.on_stop_clicked)
        self.ui.btnClear.clicked.connect(self.on_clear_clicked)

        self.timer.timeout.connect(self.update_view)
        self.ui.spinBoxSampleRate.editingFinished.connect(self.on_spinbox_sample_rate_editing_finished)

        self.ui.spinBoxGain.editingFinished.connect(self.on_spinbox_gain_editing_finished)
        self.ui.spinBoxGain.valueChanged.connect(self.on_spinbox_gain_value_changed)
        self.ui.sliderGain.valueChanged.connect(self.on_slider_gain_value_changed)
        self.ui.spinBoxIFGain.editingFinished.connect(self.on_spinbox_if_gain_editing_finished)
        self.ui.spinBoxIFGain.valueChanged.connect(self.on_spinbox_if_gain_value_changed)
        self.ui.sliderIFGain.valueChanged.connect(self.on_slider_if_gain_value_changed)
        self.ui.spinBoxBasebandGain.editingFinished.connect(self.on_spinbox_baseband_gain_editing_finished)
        self.ui.spinBoxBasebandGain.valueChanged.connect(self.on_spinbox_baseband_gain_value_changed)
        self.ui.sliderBasebandGain.valueChanged.connect(self.on_slider_baseband_gain_value_changed)

        self.ui.spinBoxFreq.editingFinished.connect(self.on_spinbox_frequency_editing_finished)
        self.ui.spinBoxBandwidth.editingFinished.connect(self.on_spinbox_bandwidth_editing_finished)
        self.ui.spinBoxPort.editingFinished.connect(self.on_spinbox_port_editing_finished)
        self.ui.lineEditIP.editingFinished.connect(self.on_spinbox_ip_editing_finished)

        self.ui.spinBoxFreqCorrection.editingFinished.connect(self.on_spinbox_freq_correction_editing_finished)
        self.ui.comboBoxDirectSampling.currentIndexChanged.connect(self.on_combobox_direct_sampling_index_changed)

        self.ui.cbDevice.currentIndexChanged.connect(self.on_selected_device_changed)
        self.ui.sliderYscale.valueChanged.connect(self.on_slider_y_scale_value_changed)

        self.ui.btnLockBWSR.clicked.connect(self.on_btn_lock_bw_sr_clicked)

        self.sync_gain_sliders()

    def _create_device_connects(self):
        self.device.stopped.connect(self.on_device_stopped)
        self.device.started.connect(self.on_device_started)
        self.device.sender_needs_restart.connect(self._restart_device_thread)

    def reset(self):
        self.ui.txtEditErrors.clear()
        self.device.current_index = 0
        self.device.current_iteration = 0
        self.ui.lSamplesCaptured.setText("0")
        self.ui.lSignalSize.setText("0")
        self.ui.lTime.setText("0")
        self.ui.lblCurrentRepeatValue.setText("-")
        self.scene_manager.set_text("")
        self.ui.progressBar.setValue(0)
        self.ui.btnClear.setEnabled(False)
        self.ui.btnSave.setEnabled(False)

    def init_device(self):
        pass

    def save_before_close(self):
        return True

    @pyqtSlot()
    def on_spinbox_sample_rate_editing_finished(self):
        self.device.sample_rate = self.ui.spinBoxSampleRate.value()
        if self.bw_sr_are_locked:
            self.ui.spinBoxBandwidth.setValue(self.ui.spinBoxSampleRate.value())
            self.device.bandwidth = self.ui.spinBoxBandwidth.value()

    @pyqtSlot()
    def on_spinbox_frequency_editing_finished(self):
        self.device.frequency = self.ui.spinBoxFreq.value()

    @pyqtSlot()
    def on_spinbox_bandwidth_editing_finished(self):
        self.device.bandwidth = self.ui.spinBoxBandwidth.value()
        if self.bw_sr_are_locked:
            self.ui.spinBoxSampleRate.setValue(self.ui.spinBoxBandwidth.value())
            self.device.sample_rate = self.ui.spinBoxSampleRate.value()

    @pyqtSlot()
    def on_spinbox_ip_editing_finished(self):
        self.device.ip = self.ui.lineEditIP.text()

    @pyqtSlot()
    def on_spinbox_port_editing_finished(self):
        self.device.port = self.ui.spinBoxPort.value()

    @pyqtSlot()
    def on_spinbox_freq_correction_editing_finished(self):
        self.device.freq_correction = self.ui.spinBoxFreqCorrection.value()

    @pyqtSlot(int)
    def on_combobox_direct_sampling_index_changed(self, index: int):
        self.device.direct_sampling_mode = index

    @pyqtSlot()
    def on_spinbox_gain_editing_finished(self):
        self.device.gain = self.ui.spinBoxGain.value()

    @pyqtSlot(int)
    def on_spinbox_gain_value_changed(self, value: int):
        dev_conf = self.get_config_for_selected_device()
        try:
            self.ui.sliderGain.setValue(dev_conf[self.rx_tx_prefix + "rf_gain"].index(value))
        except (ValueError, KeyError):
            pass

    @pyqtSlot(int)
    def on_slider_gain_value_changed(self, value: int):
        dev_conf = self.get_config_for_selected_device()
        self.ui.spinBoxGain.setValue(dev_conf[self.rx_tx_prefix + "rf_gain"][value])

    @pyqtSlot()
    def on_spinbox_if_gain_editing_finished(self):
        self.device.if_gain = self.ui.spinBoxIFGain.value()

    @pyqtSlot(int)
    def on_slider_if_gain_value_changed(self, value: int):
        dev_conf = self.get_config_for_selected_device()
        self.ui.spinBoxIFGain.setValue(dev_conf[self.rx_tx_prefix + "if_gain"][value])

    @pyqtSlot(int)
    def on_spinbox_if_gain_value_changed(self, value: int):
        dev_conf = self.get_config_for_selected_device()
        try:
            self.ui.sliderIFGain.setValue(dev_conf[self.rx_tx_prefix + "if_gain"].index(value))
        except (ValueError, KeyError):
            pass

    @pyqtSlot()
    def on_spinbox_baseband_gain_editing_finished(self):
        self.device.baseband_gain = self.ui.spinBoxBasebandGain.value()

    @pyqtSlot(int)
    def on_slider_baseband_gain_value_changed(self, value: int):
        dev_conf = self.get_config_for_selected_device()
        self.ui.spinBoxBasebandGain.setValue(dev_conf[self.rx_tx_prefix + "baseband_gain"][value])

    @pyqtSlot(int)
    def on_spinbox_baseband_gain_value_changed(self, value: int):
        dev_conf = self.get_config_for_selected_device()
        try:
            self.ui.sliderBasebandGain.setValue(dev_conf[self.rx_tx_prefix + "baseband_gain"].index(value))
        except (ValueError, KeyError):
            pass

    @pyqtSlot()
    def on_selected_device_changed(self):
        dev_name = self.ui.cbDevice.currentText()

        self.init_device()

        self.graphics_view.scene_manager = self.scene_manager
        self.graphics_view.setScene(self.scene_manager.scene)
        self.set_device_ui_items_visibility(dev_name)
        self.sync_gain_sliders()

        # Set default IPs for USRP and RTLSDRTCP
        if dev_name == "USRP":
            if self.ui.lineEditIP.text() == constants.DEFAULT_IP_RTLSDRTCP:
                self.ui.lineEditIP.setText(constants.DEFAULT_IP_USRP)
        elif dev_name == "RTL-TCP":
            if self.ui.lineEditIP.text() == constants.DEFAULT_IP_USRP:
                self.ui.lineEditIP.setText(constants.DEFAULT_IP_RTLSDRTCP)

        self.set_bandwidth_status()

    @pyqtSlot()
    def on_start_clicked(self):
        self.emit_editing_finished_signals()

    @pyqtSlot()
    def on_stop_clicked(self):
        self.device.stop("Stopped receiving: Stop button clicked")

    @pyqtSlot()
    def on_device_stopped(self):
        self.graphics_view.capturing_data = False
        self.set_device_ui_items_enabled(True)
        self.ui.btnStart.setEnabled(True)
        self.ui.btnStop.setEnabled(False)
        self.ui.btnClear.setEnabled(True)
        self.ui.btnSave.setEnabled(self.device.current_index > 0)

        self.timer.stop()
        self.scene_manager.set_text("")
        self.update_view()

    @pyqtSlot()
    def on_device_started(self):
        self.ui.txtEditErrors.clear()
        self.scene_manager.set_text("Waiting for device..")
        self.graphics_view.capturing_data = True
        self.ui.btnSave.setEnabled(False)
        self.ui.btnStart.setEnabled(False)

        self.ui.btnClear.setEnabled(False)
        self.ui.spinBoxNRepeat.setEnabled(False)
        self.ui.btnStop.setEnabled(True)

        self.timer.start(self.update_interval)

    def update_view(self):
        self.ui.sliderYscale.setValue(int(self.graphics_view.transform().m22()))

        txt = self.ui.txtEditErrors.toPlainText()
        new_errors = self.device.read_errors()

        if "No devices found for" in new_errors:
            self.device.stop_on_error("Could not establish connection to USRP")
            Errors.usrp_ip_not_found()

            self.on_clear_clicked()

        elif "OSError" in new_errors:
            self.device.stop_on_error("OSError")
            self.on_clear_clicked()

        elif "FATAL: No supported devices found" in new_errors or \
                        "HACKRF_ERROR_NOT_FOUND" in new_errors or \
                        "HACKRF_ERROR_LIBUSB" in new_errors:
            self.device.stop_on_error("Could not establish connection to HackRF")
            Errors.hackrf_not_found()
            self.on_clear_clicked()

        elif "No module named gnuradio" in new_errors:
            self.device.stop_on_error("Did not find gnuradio.")
            Errors.gnuradio_not_installed()
            self.on_clear_clicked()

        elif "RTLSDR-open: Error Code: -1" in new_errors:
            self.device.stop_on_error("Could not open a RTL-SDR device.")
            self.on_clear_clicked()

        elif "Address already in use" in new_errors:
            self._restart_device_thread()

        if len(new_errors) > 1:
            self.ui.txtEditErrors.setPlainText(txt + new_errors)

        self.ui.progressBar.setValue(self.device.current_index)

        self.ui.lSamplesCaptured.setText("{0:n}".format(self.device.current_index))
        self.ui.lSignalSize.setText(locale.format_string("%.2f", (8 * self.device.current_index) / (1024 ** 2)))
        self.ui.lTime.setText(locale.format_string("%.2f", self.device.current_index / self.device.sample_rate))

        if self.is_rx:
            self.ui.labelReceiveBufferFull.setText("{0}%".format(int(100 * self.device.current_index /
                                                                     len(self.device.data))))

        if self.device.current_index == 0:
            return False

        return True

    def _restart_device_thread(self):
        self.device.stop("Restarting with new port")
        QApplication.processEvents()

        self.device.port = random.randint(1024, 65536)
        logger.info("Retry with port " + str(self.device.port))

        self.device.start()
        QApplication.processEvents()

    @pyqtSlot()
    def on_clear_clicked(self):
        pass

    def closeEvent(self, event: QCloseEvent):
        self.emit_editing_finished_signals()
        if self.device.backend == Backends.network:
            event.accept()
            return

        self.device.stop("Dialog closed. Killing recording process.")
        logger.debug("Device stopped successfully.")
        if not self.save_before_close():
            event.ignore()
            return

        time.sleep(0.1)
        if self.device.backend != Backends.none:
            # Backend none is selected, when no device is available
            logger.debug("Cleaning up device")
            self.device.cleanup()
            logger.debug("Successfully cleaned up device")
            self.recording_parameters.emit(str(self.device.name), dict(frequency=self.device.frequency,
                                                                       sample_rate=self.device.sample_rate,
                                                                       bandwidth=self.device.bandwidth,
                                                                       gain=self.device.gain,
                                                                       if_gain=self.device.if_gain,
                                                                       baseband_gain=self.device.baseband_gain,
                                                                       freq_correction=self.device.freq_correction
                                                                       ))

        event.accept()

    @pyqtSlot()
    def on_btn_lock_bw_sr_clicked(self):
        self.bw_sr_are_locked = self.ui.btnLockBWSR.isChecked()
        constants.SETTINGS.setValue("lock_bandwidth_sample_rate", self.bw_sr_are_locked)
        if self.bw_sr_are_locked:
            self.ui.btnLockBWSR.setIcon(QIcon(":/icons/data/icons/lock.svg"))
            self.ui.spinBoxBandwidth.setValue(self.ui.spinBoxSampleRate.value())
            self.ui.spinBoxBandwidth.editingFinished.emit()
        else:
            self.ui.btnLockBWSR.setIcon(QIcon(":/icons/data/icons/unlock.svg"))

    @pyqtSlot(int)
    def on_slider_y_scale_value_changed(self, new_value: int):
        # Scale Up = Top Half, Scale Down = Lower Half
        transform = self.graphics_view.transform()
        self.graphics_view.setTransform(QTransform(transform.m11(), transform.m12(), transform.m13(),
                                                   transform.m21(), new_value, transform.m23(),
                                                   transform.m31(), transform.m32(), transform.m33()))
