import ctypes
import os
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QRadioButton, QButtonGroup,
    QVBoxLayout, QWidget
)


@dataclass(frozen=True)
class DNSProfile:
    name: str
    primary: str
    secondary: str
    doh: Optional[str] = None


PROFILES = [
    DNSProfile(
        "Comss.one",
        "83.220.169.155",
        "212.109.195.93",
        "https://dns.comss.one/dns-query",
    ),
    DNSProfile(
        "Xbox DNS",
        "111.88.96.50",
        "111.88.96.51",
        "https://xbox-dns.ru/dns-query",
    ),
    DNSProfile(
        "Malw DNS",
        "95.216.204.218",
        "80.253.249.40",
        "https://dns.malw.link/dns-query",
    ),
    DNSProfile(
        "GeoHide DNS",
        "45.155.204.190",
        "37.230.192.51",
        None,  # DoH не включаем без подтверждённого шаблона
    ),
]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = ""
    else:
        executable = sys.executable
        params = f'"{os.path.abspath(sys.argv[0])}"'
        if len(sys.argv) > 1:
            params += " " + " ".join(f'"{a}"' for a in sys.argv[1:])

    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, params, None, 1
    )
    if rc <= 32:
        raise RuntimeError("Не удалось получить права администратора.")
    sys.exit(0)


def run_ps(script: str) -> tuple[int, str, str]:
    cp = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return cp.returncode, cp.stdout.strip(), cp.stderr.strip()


def get_adapters():
    script = r"""
Get-NetAdapter |
Where-Object { $_.Status -eq 'Up' -and $_.HardwareInterface -eq $true } |
Select-Object -ExpandProperty Name
"""
    rc, out, err = run_ps(script)
    if rc != 0:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def get_dns_servers(adapter: str):
    safe = adapter.replace("'", "''")
    script = f"""
(Get-DnsClientServerAddress -InterfaceAlias '{safe}' -AddressFamily IPv4).ServerAddresses
"""
    rc, out, err = run_ps(script)
    if rc != 0:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def set_dns(adapter: str, profile: DNSProfile, enable_doh: bool):
    safe = adapter.replace("'", "''")
    servers = f'@("{profile.primary}","{profile.secondary}")'
    script = f"""
Set-DnsClientServerAddress -InterfaceAlias '{safe}' -ServerAddresses {servers}
"""
    rc, out, err = run_ps(script)
    if rc != 0:
        raise RuntimeError(err or "Не удалось изменить DNS.")

    if enable_doh and profile.doh:
        for ip in (profile.primary, profile.secondary):
            # Удаляем старую запись шифрования, если она существует.
            subprocess.run(
                ["netsh", "dnsclient", "delete", "encryption", f"server={ip}"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            cp = subprocess.run(
                [
                    "netsh", "dnsclient", "add", "encryption",
                    f"server={ip}",
                    f"dohtemplate={profile.doh}",
                    "autoupgrade=yes",
                    "udpfallback=yes",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # Некоторые сборки Windows могут не поддерживать эту команду.
            # DNS при этом уже применён обычным способом, поэтому не считаем это фатальной ошибкой.

    subprocess.run(
        ["ipconfig", "/flushdns"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def set_dhcp(adapter: str):
    safe = adapter.replace("'", "''")
    script = f"""
Set-DnsClientServerAddress -InterfaceAlias '{safe}' -ResetServerAddresses
"""
    rc, out, err = run_ps(script)
    if rc != 0:
        raise RuntimeError(err or "Не удалось вернуть автоматический DNS.")
    subprocess.run(
        ["ipconfig", "/flushdns"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def make_dns_query(hostname="chatgpt.com"):
    tid = os.urandom(2)
    flags = b"\x01\x00"
    counts = b"\x00\x01\x00\x00\x00\x00\x00\x00"
    qname = b"".join(bytes([len(part)]) + part.encode("ascii") for part in hostname.split(".")) + b"\x00"
    qtype_qclass = b"\x00\x01\x00\x01"
    return tid + flags + counts + qname + qtype_qclass


def dns_latency(ip: str, timeout=1.5):
    packet = make_dns_query()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        start = time.perf_counter()
        sock.sendto(packet, (ip, 53))
        data, _ = sock.recvfrom(2048)
        elapsed = (time.perf_counter() - start) * 1000
        if len(data) >= 12:
            return round(elapsed)
    except OSError:
        return None
    finally:
        sock.close()
    return None


class TestWorker(QObject):
    result = Signal(str, object)
    finished = Signal()

    def run(self):
        for p in PROFILES:
            t1 = dns_latency(p.primary)
            t2 = dns_latency(p.secondary)
            vals = [x for x in (t1, t2) if x is not None]
            best = min(vals) if vals else None
            self.result.emit(p.name, best)
        self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SPAIK DNS SWITCHER")
        self.setMinimumSize(620, 560)
        self.worker_thread = None
        self.latencies = {}
        self.rows = {}

        self.setStyleSheet("""
            QMainWindow { background: #11151c; color: #eef2f7; }
            QLabel { color: #eef2f7; }
            QComboBox {
                background: #1b2230; color: #eef2f7; border: 1px solid #344055;
                padding: 8px; border-radius: 8px;
            }
            QPushButton {
                background: #263247; color: white; border: none;
                padding: 10px 14px; border-radius: 9px; font-weight: 600;
            }
            QPushButton:hover { background: #33425d; }
            QPushButton:disabled { background: #202735; color: #70798a; }
            QFrame#card {
                background: #171d27; border: 1px solid #293243;
                border-radius: 12px;
            }
            QRadioButton { color: #eef2f7; font-size: 14px; }
        """)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("SPAIK DNS SWITCHER")
        title.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Быстрое переключение GeoDNS для AI-сервисов")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color:#9eabc0; margin-bottom:8px;")
        layout.addWidget(subtitle)

        top = QHBoxLayout()
        top.addWidget(QLabel("Сетевой адаптер:"))
        self.adapter_combo = QComboBox()
        self.adapter_combo.currentTextChanged.connect(self.refresh_current_dns)
        top.addWidget(self.adapter_combo, 1)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.load_adapters)
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)

        self.group = QButtonGroup(self)
        for i, p in enumerate(PROFILES):
            row = QHBoxLayout()
            radio = QRadioButton(p.name)
            if i == 0:
                radio.setChecked(True)
            self.group.addButton(radio, i)
            row.addWidget(radio, 1)

            ips = QLabel(f"{p.primary} / {p.secondary}")
            ips.setStyleSheet("color:#8592a8; font-size:11px;")
            row.addWidget(ips)

            latency = QLabel("—")
            latency.setFixedWidth(75)
            latency.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(latency)

            card_layout.addLayout(row)
            self.rows[p.name] = (radio, latency)

        layout.addWidget(card)

        option_row = QHBoxLayout()
        self.doh_btn = QPushButton("DoH: ВКЛ")
        self.doh_btn.setCheckable(True)
        self.doh_btn.setChecked(True)
        self.doh_btn.clicked.connect(self.update_doh_label)
        option_row.addWidget(self.doh_btn)

        self.test_btn = QPushButton("⚡ Проверить все DNS")
        self.test_btn.clicked.connect(self.start_test)
        option_row.addWidget(self.test_btn, 1)
        layout.addLayout(option_row)

        self.apply_btn = QPushButton("ПРИМЕНИТЬ ВЫБРАННЫЙ DNS")
        self.apply_btn.setMinimumHeight(46)
        self.apply_btn.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_btn)

        self.best_btn = QPushButton("Выбрать и применить самый быстрый")
        self.best_btn.clicked.connect(self.apply_best)
        layout.addWidget(self.best_btn)

        self.dhcp_btn = QPushButton("Вернуть автоматический DNS (DHCP)")
        self.dhcp_btn.clicked.connect(self.restore_dhcp)
        layout.addWidget(self.dhcp_btn)

        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QGridLayout(status_card)
        status_layout.addWidget(QLabel("Текущий DNS:"), 0, 0)
        self.current_dns = QLabel("—")
        self.current_dns.setStyleSheet("font-weight:600;")
        status_layout.addWidget(self.current_dns, 0, 1)
        status_layout.addWidget(QLabel("Статус:"), 1, 0)
        self.status = QLabel("Готово")
        self.status.setStyleSheet("color:#8bd89b; font-weight:600;")
        status_layout.addWidget(self.status, 1, 1)
        layout.addWidget(status_card)

        footer = QLabel("Для изменения DNS нужны права администратора. После смены DNS кэш автоматически очищается.")
        footer.setWordWrap(True)
        footer.setStyleSheet("color:#778397; font-size:11px;")
        layout.addWidget(footer)

        self.load_adapters()

    def update_doh_label(self):
        self.doh_btn.setText("DoH: ВКЛ" if self.doh_btn.isChecked() else "DoH: ВЫКЛ")

    def load_adapters(self):
        current = self.adapter_combo.currentText()
        self.adapter_combo.blockSignals(True)
        self.adapter_combo.clear()
        adapters = get_adapters()
        self.adapter_combo.addItems(adapters)
        if current in adapters:
            self.adapter_combo.setCurrentText(current)
        self.adapter_combo.blockSignals(False)

        if not adapters:
            self.status.setText("Активные сетевые адаптеры не найдены")
            self.current_dns.setText("—")
        else:
            self.refresh_current_dns()

    def refresh_current_dns(self):
        adapter = self.adapter_combo.currentText()
        if not adapter:
            return
        servers = get_dns_servers(adapter)
        if not servers:
            self.current_dns.setText("Автоматически / не определён")
            return

        text = ", ".join(servers)
        matched = None
        for p in PROFILES:
            if p.primary in servers or p.secondary in servers:
                matched = p.name
                break
        self.current_dns.setText(f"{matched} — {text}" if matched else text)

    def selected_profile(self):
        idx = self.group.checkedId()
        if idx < 0:
            return None
        return PROFILES[idx]

    def apply_selected(self):
        adapter = self.adapter_combo.currentText()
        p = self.selected_profile()
        if not adapter or not p:
            QMessageBox.warning(self, "SPAIK DNS SWITCHER", "Выберите адаптер и DNS.")
            return
        try:
            self.status.setText(f"Применяю {p.name}...")
            QApplication.processEvents()
            set_dns(adapter, p, self.doh_btn.isChecked())
            self.status.setText(f"✓ {p.name} активирован")
            self.refresh_current_dns()

            if self.doh_btn.isChecked() and not p.doh:
                QMessageBox.information(
                    self,
                    "DNS применён",
                    f"{p.name} применён.\n\nДля этого профиля DoH не включён, "
                    "потому что в программе нет подтверждённого DoH-шаблона."
                )
        except Exception as e:
            self.status.setText("Ошибка")
            QMessageBox.critical(self, "Ошибка", str(e))

    def restore_dhcp(self):
        adapter = self.adapter_combo.currentText()
        if not adapter:
            return
        try:
            self.status.setText("Возвращаю автоматический DNS...")
            QApplication.processEvents()
            set_dhcp(adapter)
            self.status.setText("✓ Автоматический DNS восстановлен")
            self.refresh_current_dns()
        except Exception as e:
            self.status.setText("Ошибка")
            QMessageBox.critical(self, "Ошибка", str(e))

    def start_test(self):
        if self.worker_thread and self.worker_thread.isRunning():
            return
        self.latencies.clear()
        for _, lbl in self.rows.values():
            lbl.setText("тест...")
        self.test_btn.setEnabled(False)
        self.best_btn.setEnabled(False)
        self.status.setText("Проверяю DNS...")

        self.worker_thread = QThread()
        self.worker = TestWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.result.connect(self.on_test_result)
        self.worker.finished.connect(self.on_test_finished)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def on_test_result(self, name, latency):
        self.latencies[name] = latency
        label = self.rows[name][1]
        if latency is None:
            label.setText("нет ответа")
            label.setStyleSheet("color:#ef8585;")
        else:
            label.setText(f"{latency} ms")
            label.setStyleSheet("color:#8bd89b;")

    def on_test_finished(self):
        self.test_btn.setEnabled(True)
        self.best_btn.setEnabled(True)
        good = {k: v for k, v in self.latencies.items() if v is not None}
        if good:
            name = min(good, key=good.get)
            self.status.setText(f"Самый быстрый сейчас: {name} ({good[name]} ms)")
        else:
            self.status.setText("DNS-серверы не ответили на UDP/53")

    def apply_best(self):
        good = {k: v for k, v in self.latencies.items() if v is not None}
        if not good:
            QMessageBox.information(
                self,
                "SPAIK DNS SWITCHER",
                "Сначала нажмите «Проверить все DNS»."
            )
            return
        best_name = min(good, key=good.get)
        for i, p in enumerate(PROFILES):
            if p.name == best_name:
                self.group.button(i).setChecked(True)
                break
        self.apply_selected()


def main():
    if os.name != "nt":
        print("Эта программа предназначена для Windows.")
        sys.exit(1)

    if not is_admin():
        try:
            relaunch_as_admin()
        except Exception as e:
            ctypes.windll.user32.MessageBoxW(None, str(e), "SPAIK DNS SWITCHER", 0x10)
            sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("SPAIK DNS SWITCHER")
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
