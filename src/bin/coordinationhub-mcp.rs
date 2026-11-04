fn main() {
    let storage = std::env::var("COORDINATIONHUB_STORAGE_DIR")
        .ok()
        .map(std::path::PathBuf::from);
    let project = std::env::var("COORDINATIONHUB_PROJECT_ROOT")
        .ok()
        .map(std::path::PathBuf::from);
    let namespace = std::env::var("COORDINATIONHUB_NAMESPACE").unwrap_or_else(|_| "hub".into());
    let hub = match coordinationhub::Hub::open(storage.as_deref(), project.as_deref(), &namespace) {
        Ok(h) => h,
        Err(err) => {
            eprintln!("Error: {err}");
            std::process::exit(1);
        }
    };
    if let Err(err) = coordinationhub::mcp::serve_stdio(&hub) {
        eprintln!("Error: {err}");
        std::process::exit(1);
    }
}
