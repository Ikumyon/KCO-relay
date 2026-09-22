use std::cell::RefCell;
use std::io::Write;
use std::sync::mpsc::{self, Sender};
use std::time::Instant;
use serde::Serialize;
use windows_sys::Win32::Foundation::FILETIME;
use windows_sys::Win32::System::Threading::{GetCurrentThread, GetThreadTimes};

#[derive(Serialize)]
struct Sample {
    kind: &'static str,
    start_us: u64,
    duration_us: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    geometry: Option<[i32; 6]>,
}

#[derive(Serialize)]
struct Report {
    elapsed_us: u64,
    ui_cpu_us: Option<u64>,
    dropped_samples: usize,
    samples: Vec<Sample>,
}

struct Session {
    start: Instant,
    cpu_start: Option<u64>,
    samples: Vec<Sample>,
    dropped_samples: usize,
}

thread_local! {
    static OUTPUT: RefCell<Option<Sender<Report>>> = const { RefCell::new(None) };
    static SESSION: RefCell<Option<Session>> = const { RefCell::new(None) };
}

fn thread_cpu_us() -> Option<u64> {
    let mut times = [FILETIME { dwLowDateTime: 0, dwHighDateTime: 0 }; 4];
    unsafe {
        if GetThreadTimes(GetCurrentThread(), &mut times[0], &mut times[1], &mut times[2], &mut times[3]) == 0 {
            return None;
        }
    }
    let value = |t: FILETIME| ((t.dwHighDateTime as u64) << 32) | t.dwLowDateTime as u64;
    Some((value(times[2]) + value(times[3])) / 10)
}

pub fn install(window: &slint::Window) -> Result<(), Box<dyn std::error::Error>> {
    let diagnostics = std::env::args().any(|arg| arg == "--diagnose-resize");
    // Keep the renderer path identical for normal and diagnostic launches.
    // Slint changes partial rendering / command submission when a notifier exists.
    let notifier = window.set_rendering_notifier(move |state, _| {
        if !diagnostics { return; }
        let name = match state {
            slint::RenderingState::BeforeRendering => "render_begin",
            slint::RenderingState::AfterRendering => "render_end_before_present",
            _ => return,
        };
        if let Some(start) = timestamp() {
            record(name, start);
        }
    });
    if !diagnostics {
        return Ok(());
    }
    let path = std::env::temp_dir().join("kco-relay-resize.jsonl");
    let mut output = std::fs::OpenOptions::new().create(true).append(true).open(path)?;
    // The rendering callback excludes buffer presentation; WM_PAINT includes it.
    writeln!(output, "{}", serde_json::json!({
        "event": "diagnostics_start", "pid": std::process::id(),
        "time": chrono::Utc::now().to_rfc3339(),
        "render_notifier": format!("{notifier:?}"),
        "executable": std::env::current_exe().ok(),
    }))?;
    let (sender, receiver) = mpsc::channel::<Report>();
    std::thread::Builder::new().name("resize-diagnostics".into()).spawn(move || {
        for report in receiver {
            if serde_json::to_writer(&mut output, &report).is_err()
                || writeln!(output).is_err() || output.flush().is_err() {
                break;
            }
        }
    })?;
    OUTPUT.with(|slot| *slot.borrow_mut() = Some(sender));
    Ok(())
}

pub fn begin() {
    if OUTPUT.with(|slot| slot.borrow().is_none()) { return; }
    SESSION.with(|slot| *slot.borrow_mut() = Some(Session {
        start: Instant::now(), cpu_start: thread_cpu_us(),
        samples: Vec::with_capacity(32768), dropped_samples: 0,
    }));
}

pub fn timestamp() -> Option<Instant> {
    SESSION.with(|slot| slot.borrow().as_ref().map(|_| Instant::now()))
}

pub fn record(kind: &'static str, start: Instant) {
    record_geometry(kind, start, None);
}

pub fn record_geometry(kind: &'static str, start: Instant, geometry: Option<[i32; 6]>) {
    let end = Instant::now();
    SESSION.with(|slot| {
        if let Some(session) = slot.borrow_mut().as_mut() {
            if session.samples.len() == 32768 {
                session.dropped_samples += 1;
                return;
            }
            session.samples.push(Sample {
                kind, start_us: start.duration_since(session.start).as_micros() as u64,
                duration_us: end.duration_since(start).as_micros() as u64,
                geometry,
            });
        }
    });
}

pub fn finish() {
    let session = SESSION.with(|slot| slot.borrow_mut().take());
    if let Some(session) = session {
        let report = Report {
            elapsed_us: session.start.elapsed().as_micros() as u64,
            ui_cpu_us: thread_cpu_us().zip(session.cpu_start).map(|(end, start)| end.saturating_sub(start)),
            dropped_samples: session.dropped_samples, samples: session.samples,
        };
        OUTPUT.with(|slot| {
            if let Some(sender) = slot.borrow().as_ref() { let _ = sender.send(report); }
        });
    }
}
