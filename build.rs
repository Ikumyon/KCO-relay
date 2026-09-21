fn main() {
    slint_build::compile("ui/appwindow.slint").unwrap();

    if std::env::var_os("CARGO_CFG_WINDOWS").is_some() {
        let mut res = winres::WindowsResource::new();
        res.set_icon("icons/icon.ico");
        res.set_icon_with_id("icons/icon.ico", "tray-default");
        res.compile().unwrap();
    }
}
