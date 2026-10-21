import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.QApplication.instance() or main.QApplication([])

    def test_existing_profiles_are_unchanged(self):
        self.assertEqual(
            [(p.name, p.primary, p.secondary, p.doh) for p in main.PROFILES[:4]],
            [
                ("Comss.one", "83.220.169.155", "212.109.195.93", "https://dns.comss.one/dns-query"),
                ("Xbox DNS", "111.88.96.50", "111.88.96.51", "https://xbox-dns.ru/dns-query"),
                ("Malw DNS", "95.216.204.218", "80.253.249.40", "https://dns.malw.link/dns-query"),
                ("GeoHide DNS", "45.155.204.190", "37.230.192.51", None),
            ],
        )
        self.assertEqual(len(main.PROFILES), 6)

    def make_window(self):
        with patch.object(main, "get_adapters", return_value=["Test adapter"]), \
             patch.object(main, "get_dns_servers", return_value=["192.0.2.1"]):
            return main.MainWindow()

    def test_new_radio_buttons_apply_real_ip_and_doh_commands(self):
        # Exercise the real UI handler and set_dns, mocking only Windows commands.
        for index in (4, 5):
            for doh_enabled in (False, True):
                with self.subTest(profile=index, doh=doh_enabled):
                    window = self.make_window()
                    profile = main.PROFILES[index]
                    window.group.button(index).click()
                    window.doh_btn.setChecked(doh_enabled)
                    with patch.object(main, "run_ps", return_value=(0, "", "")) as ps, \
                         patch.object(main, "get_dns_servers", return_value=list(profile.servers)), \
                         patch.object(main.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                         patch.object(main.QMessageBox, "critical") as error:
                        window.apply_btn.click()
                        error.assert_not_called()
                        command = ps.call_args.args[0]
                        expected = '@(' + ','.join(f'"{ip}"' for ip in profile.servers) + ')'
                        self.assertIn("Set-DnsClientServerAddress", command)
                        self.assertIn("-ServerAddresses " + expected, command)
                        self.assertIn("Test adapter", command)
                        self.assertNotIn("None", command)
                        additions = [call.args[0] for call in run.call_args_list
                                     if call.args[0][:4] == ["netsh", "dnsclient", "add", "encryption"]]
                        self.assertEqual(len(additions), len(profile.servers) if doh_enabled else 0)
                        for argv, ip in zip(additions, profile.servers):
                            self.assertIn("server=" + ip, argv)
                            self.assertIn("dohtemplate=" + profile.doh, argv)
                        self.assertIn("активирован", window.status.text())
                        self.assertIn(profile.name, window.current_dns.text())
                    window.close()

    def test_readback_mismatch_is_not_reported_as_success(self):
        with patch.object(main, "run_ps", return_value=(0, "", "")), \
             patch.object(main.subprocess, "run"), \
             patch.object(main, "get_dns_servers", return_value=["192.0.2.1"]):
            with self.assertRaisesRegex(RuntimeError, "Windows не подтвердила"):
                main.set_dns("Test adapter", main.PROFILES[4], False)

    def test_powershell_failure_does_not_run_doh(self):
        with patch.object(main, "run_ps", return_value=(1, "", "access denied")), \
             patch.object(main.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "access denied"):
                main.set_dns("Test adapter", main.PROFILES[5], True)
            run.assert_not_called()

    def test_doh_failure_is_not_reported_as_success(self):
        with patch.object(main, "run_ps", return_value=(0, "", "")), \
             patch.object(main.subprocess, "run", return_value=Mock(
                 returncode=1, stderr="DoH is unavailable", stdout="")):
            with self.assertRaisesRegex(RuntimeError, "не смогла включить DoH"):
                main.set_dns("Test adapter", main.PROFILES[5], True)

    def test_benchmark_queries_every_configured_server(self):
        worker = main.TestWorker()
        results = []
        worker.result.connect(lambda name, latency: results.append((name, latency)))
        with patch.object(main, "dns_latency", return_value=12) as latency:
            worker.run()
            self.assertEqual([c.args[0] for c in latency.call_args_list],
                             [ip for profile in main.PROFILES for ip in profile.servers])
        self.assertEqual([name for name, _ in results], [p.name for p in main.PROFILES])

    def test_fastest_selection_can_apply_each_new_profile(self):
        for profile in main.PROFILES[4:]:
            with self.subTest(profile=profile.name):
                window = self.make_window()
                window.latencies = {main.PROFILES[0].name: 50, profile.name: 5}
                with patch.object(main, "set_dns") as apply, \
                     patch.object(main, "get_dns_servers", return_value=list(profile.servers)):
                    window.best_btn.click()
                    apply.assert_called_once_with("Test adapter", profile, True)
                    self.assertEqual(window.selected_profile(), profile)
                window.close()


if __name__ == "__main__":
    unittest.main()
