// Move this Mac onto a Wi-Fi network's 5 GHz access point. macOS picks the band by itself and has no setting for it,
// and some networks keep their 6 GHz clients apart from everyone else, so phones and the Quest can't reach the Mac.
//
//   swift scripts/wifi-5ghz.swift "xTribe Guest"
//
// It asks for the Wi-Fi password (not shown, not stored). If joining fails, macOS rejoins its usual access point.
import CoreWLAN
import Foundation

guard CommandLine.arguments.count > 1 else {
  print("usage: swift scripts/wifi-5ghz.swift \"<network name>\"")
  exit(2)
}
let ssid = CommandLine.arguments[1]
let iface = CWWiFiClient.shared().interface()!

var target: CWNetwork?
for _ in 1...6 where target == nil {  // one scan doesn't always see every access point
  let found = (try? iface.scanForNetworks(withName: ssid)) ?? []
  target = found.filter { $0.wlanChannel?.channelBand == .band5GHz }.max { $0.rssiValue < $1.rssiValue }
}
guard let target else {
  print("No 5 GHz access point for \"\(ssid)\" in range.")
  exit(1)
}

let password = String(cString: getpass("Wi-Fi password for \(ssid): "))
do {
  try iface.associate(to: target, password: password)
  let channel = iface.wlanChannel()
  print("Joined \(ssid) on channel \(channel?.channelNumber ?? -1)", channel?.channelBand == .band5GHz ? "(5 GHz)." : "(not 5 GHz).")
} catch {
  print("Couldn't join: \(error.localizedDescription). macOS will rejoin its usual access point by itself.")
  exit(1)
}
