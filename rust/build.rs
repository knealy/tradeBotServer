fn main() {
    // Tell cargo to link against Python
    pyo3_build_config::use_pyo3_cfgs();
    
    // On macOS with Homebrew Python framework, link directly against the dylib
    if cfg!(target_os = "macos") {
        // Get the Python library directory from the active Python
        if let Ok(output) = std::process::Command::new("python3")
            .arg("-c")
            .arg("import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')")
            .output()
        {
            if let Ok(libdir) = String::from_utf8(output.stdout) {
                let libdir = libdir.trim();
                if !libdir.is_empty() {
                    // Add the lib directory to the search path
                    println!("cargo:rustc-link-search=native={}", libdir);
                    
                    // Try to find and link against libpython3.X.dylib
                    if let Ok(version_output) = std::process::Command::new("python3")
                        .arg("-c")
                        .arg("import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
                        .output()
                    {
                        if let Ok(version) = String::from_utf8(version_output.stdout) {
                            let version = version.trim();
                            let dylib_name = format!("libpython{}.dylib", version);
                            let dylib_path = format!("{}/{}", libdir, dylib_name);
                            if std::path::Path::new(&dylib_path).exists() {
                                // Link directly against the dylib (use version with dots)
                                println!("cargo:rustc-link-lib=dylib=python{}", version);
                            }
                        }
                    }
                }
            }
        }
    }
}

