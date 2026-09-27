use tauri::{
  menu::{Menu, MenuItem},
  tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
  Manager,
};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  // WebView2 (Windows) blocks getUserMedia by default and never shows a prompt.
  // This flag auto-accepts the media permission so the LiveKit call can use the
  // real microphone. Must be set before the webview is created.
  #[cfg(target_os = "windows")]
  std::env::set_var(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--use-fake-ui-for-media-stream",
  );

  tauri::Builder::default()
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // ── system tray ──────────────────────────────────────────────────────────
      let show_item = MenuItem::with_id(app, "show", "Show Aster", true, None::<&str>)?;
      let quit_item = MenuItem::with_id(app, "quit", "Quit Aster", true, None::<&str>)?;
      let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

      let icon = app.default_window_icon().expect("app icon missing").clone();
      TrayIconBuilder::new()
        .icon(icon)
        .tooltip("Aster")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
          "show" => {
            if let Some(w) = app.get_webview_window("main") {
              let _ = w.show();
              let _ = w.unminimize();
              let _ = w.set_focus();
            }
          }
          "quit" => app.exit(0),
          _ => {}
        })
        .on_tray_icon_event(|tray, event| {
          if let TrayIconEvent::Click { button: MouseButton::Left, .. } = event {
            let app = tray.app_handle();
            if let Some(w) = app.get_webview_window("main") {
              let _ = w.show();
              let _ = w.unminimize();
              let _ = w.set_focus();
            }
          }
        })
        .build(app)?;

      // ── close → hide (run in tray background instead of quitting) ────────────
      let main = app.get_webview_window("main").expect("no main window");
      main.on_window_event({
        let main_c = main.clone();
        move |event| {
          if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = main_c.hide();
          }
        }
      });

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
