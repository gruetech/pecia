//! What the process asks of the operating system: the local date, the login
//! user, entropy, and the terminal. Kept here so the core stays a pure function
//! of its input.
//!
//! The one crate in the workspace permitted `unsafe` (every other crate's
//! lints forbid it, pc-2426297cff1f), and every C type and constant it touches is the `libc`
//! crate's definition for the target being built — never a layout or a number
//! copied by hand. A copied `struct tm` or ioctl request is right on the
//! platforms its author checked and silently wrong on the next one; `libc`
//! carries each target's own, and was already in the build through sha2.

use std::ffi::CStr;

/// Today in the LOCAL time zone, YYYY-MM-DD — the reference's `date.today()`.
/// An error, never a made-up date, when the system cannot give one: this
/// stamped 1970-01-01 into created/updated/ratified on a localtime_r failure
/// (pc-5f1b7eec34a8).
pub fn today() -> Result<String, String> {
    // SAFETY: time(NULL) writes nothing.
    today_at(unsafe { libc::time(std::ptr::null_mut()) })
}

/// The local date at `t` seconds since the epoch, or why there is none.
pub fn today_at(t: libc::time_t) -> Result<String, String> {
    // SAFETY: localtime_r reads the time it is given and writes one `tm` into
    // the zeroed value passed. No pointer escapes.
    unsafe {
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&t, &mut tm).is_null() {
            return Err(format!("the system gives no local date for the time {t}, so there is no date to stamp; nothing was written"));
        }
        Ok(format!("{:04}-{:02}-{:02}", tm.tm_year as i64 + 1900, tm.tm_mon + 1, tm.tm_mday))
    }
}

/// The login user as `getpass.getuser()` finds it: LOGNAME, USER, LNAME,
/// USERNAME, then the password database.
pub fn login_user() -> String {
    for var in ["LOGNAME", "USER", "LNAME", "USERNAME"] {
        if let Ok(v) = std::env::var(var) {
            if !v.is_empty() {
                return v;
            }
        }
    }
    // SAFETY: getpwuid returns NULL or a pointer into libc's own storage, valid
    // until the next getpw* call; the name is copied out before this returns.
    unsafe {
        let pw = libc::getpwuid(libc::getuid());
        if pw.is_null() || (*pw).pw_name.is_null() {
            return "unknown".into();
        }
        CStr::from_ptr((*pw).pw_name).to_string_lossy().into_owned()
    }
}

/// PECIA_OWNER, else the login user.
pub fn default_owner() -> String {
    std::env::var("PECIA_OWNER").ok().filter(|v| !v.is_empty()).unwrap_or_else(login_user)
}

/// Eight bytes of OS entropy, hex — the mint's nonce, so two writers minting
/// the same title on the same day draw independently.
pub fn nonce_hex() -> Result<String, String> {
    use std::io::Read;
    let mut buf = [0u8; 8];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .map_err(|e| format!("no entropy for the id mint: {e}"))?;
    Ok(pecia_core::hex(&buf))
}

pub fn stdout_is_tty() -> bool {
    use std::io::IsTerminal;
    std::io::stdout().is_terminal()
}

/// The terminal's width as `shutil.get_terminal_size((100, 24))` finds it:
/// COLUMNS, then the tty, then 100.
pub fn terminal_columns() -> usize {
    if let Some(n) = std::env::var("COLUMNS").ok().and_then(|v| v.parse::<usize>().ok()).filter(|n| *n > 0) {
        return n;
    }
    let mut ws = libc::winsize { ws_row: 0, ws_col: 0, ws_xpixel: 0, ws_ypixel: 0 };
    // SAFETY: TIOCGWINSZ writes one `winsize` into the struct passed.
    let ok = unsafe { libc::ioctl(libc::STDOUT_FILENO, libc::TIOCGWINSZ, &mut ws) } == 0;
    if ok && ws.ws_col > 0 { ws.ws_col as usize } else { 100 }
}

/// Colour when asked or, absent an explicit answer, when NO_COLOR is unset,
/// TERM is not dumb, and stdout is a terminal.
pub fn supports_color(no_color_flag: bool) -> bool {
    if no_color_flag {
        return false;
    }
    if std::env::var("NO_COLOR").is_ok_and(|v| !v.is_empty()) {
        return false;
    }
    if matches!(std::env::var("TERM").unwrap_or_default().as_str(), "" | "dumb") {
        return false;
    }
    stdout_is_tty()
}

/// Unicode glyphs unless the locale names a charset other than UTF-8. The C
/// and POSIX locales count as UTF-8, as the reference's interpreter coerces
/// them — a compiled binary has no stdout encoding to ask, so the locale is
/// the signal.
pub fn supports_unicode() -> bool {
    let loc = ["LC_ALL", "LC_CTYPE", "LANG"]
        .iter()
        .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty()))
        .unwrap_or_default();
    match loc.split_once('.') {
        None => true,
        Some((_, charset)) => {
            let c = charset.split('@').next().unwrap_or("").to_ascii_lowercase().replace('-', "");
            c.starts_with("utf")
        }
    }
}

/// `os.access(path, X_OK)`: this process may execute (or, for a directory,
/// search) the path.
pub fn access_x(path: &std::path::Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(c) = std::ffi::CString::new(path.as_os_str().as_bytes()) else { return false };
    // SAFETY: access reads a NUL-terminated path and writes nothing.
    unsafe { libc::access(c.as_ptr(), libc::X_OK) == 0 }
}

/// `shutil.which`'s test: the path exists, is not a directory, and this
/// process may execute it.
pub fn executable(path: &std::path::Path) -> bool {
    access_x(path) && !path.is_dir()
}

/// Wait for a child at most `timeout`; on expiry kill it and answer None.
pub fn wait_timeout(child: &mut std::process::Child, timeout: std::time::Duration) -> std::io::Result<Option<std::process::ExitStatus>> {
    let deadline = std::time::Instant::now() + timeout;
    let mut pause = std::time::Duration::from_millis(1);
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(Some(status));
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Ok(None);
        }
        std::thread::sleep(pause);
        pause = (pause * 2).min(std::time::Duration::from_millis(50));
    }
}

/// `shutil.which`: a name with a directory part is checked where it stands;
/// a bare name is searched along PATH (the system default path when PATH is
/// unset, nothing when it is empty).
pub fn which(name: &str) -> bool {
    if name.contains('/') {
        return executable(std::path::Path::new(name));
    }
    let path = std::env::var_os("PATH").unwrap_or_else(|| "/usr/bin:/bin:/usr/sbin:/sbin".into());
    if path.is_empty() {
        return false;
    }
    std::env::split_paths(&path).any(|dir| executable(&dir.join(name)))
}

#[cfg(test)]
mod tests {
    #[test]
    fn kill_a_time_the_system_cannot_place_is_an_error_not_1970() {
        // pc-5f1b7eec34a8: on a localtime_r failure this stamped 1970-01-01.
        // A time whose year overflows an int is one localtime_r refuses.
        let err = super::today_at(libc::time_t::MAX).expect_err("no local date for time_t::MAX");
        assert!(err.contains("no local date"), "{err}");
    }

    #[test]
    fn control_an_ordinary_time_is_a_day() {
        for day in [super::today_at(86_400 * 365).expect("1971"), super::today().expect("today")] {
            let b = day.as_bytes();
            assert!(day.len() == 10 && b[4] == b'-' && b[7] == b'-', "{day}");
        }
    }
}
