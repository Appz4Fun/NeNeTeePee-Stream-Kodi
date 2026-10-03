# Copy and paste from your phone

Use a Kodi remote app to paste long API keys, usernames, and passwords into Kodi's text-entry dialog. This avoids entering them one character at a time with a television remote.

## Turn on remote control

1. Connect Kodi and your phone to the same local network.
2. Open Kodi **Settings → Services → Control**. This is under Services, rather than the System settings used for Unknown sources.
3. Turn on **Allow remote control via HTTP**. Note the port, such as `8080`.
4. Keep **Require authentication** on and set a **Username** and **Password** for remote control. These authenticate the phone to Kodi; they are separate from backend credentials.
5. If your remote app requires it, turn on **Allow remote control from applications on other systems**. Keep these remote interfaces on your local network. Kodi's [Control documentation](https://kodi.wiki/view/Settings/Services/Control) explains the HTTP and app controls.

![Kodi Services Control settings](../images/kodi/remote-control.png)

## Paste a value

1. Install [Official Kodi Remote for iOS](https://github.com/xbmc/Official-Kodi-Remote-iOS) or [Kore for Android](https://github.com/xbmc/Kore), following the project's app-store link.
2. Add your Kodi host using its local IP address, HTTP port, and the remote-control username and password. Use the app's discovery feature if it finds your device.
3. In Kodi, select the field you want to edit, such as **API Key**, to open its keyboard dialog.
4. Copy the value on your phone. Open the remote app's keyboard or text-entry control, paste the value, and send it to Kodi. The control name varies between apps.
5. Confirm the text-entry dialog, then select **OK** in the add-on settings to save it.

Masked fields remain masked in Kodi. Check that you copied the whole value and did not include spaces at either end. Remote control sends text to the active Kodi input field; open the intended field before sending it.
