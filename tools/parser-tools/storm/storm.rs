use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

// A stand-in target for measuring what probe_write.py can actually watch. It is not a model of the
// player; it is the shape that falsified a claim the player could not settle.
//
// The claim: the guard re-arm is free, so guarding many pages costs nothing (measured at 100.4% of
// unguarded throughput on 128 pages). That measurement used ONE page of a single-threaded target that
// slept between stores. This target has 24 pages hammered by four threads with no sleep at all, which
// is what a Qt player with a dozen live modules on the same pages looks like from the guard's side.
//
// Result, recorded in ticket 07: at 24, 8 and 4 guarded pages the heartbeat stops and no store is
// caught -- and the target cannot even finish its own storm. At 2 and at 1 page it reports and catches.
//
// First argument is the storm length in milliseconds. 0 is the quiet control arm, which proves the
// heartbeat can fire when nothing is contending, so that a silent storm arm means something.
fn main() {
    let storm_ms: u64 = std::env::args()
        .nth(1)
        .and_then(|a| a.parse().ok())
        .unwrap_or(12000);

    const N: usize = 24;
    let layout = std::alloc::Layout::from_size_align(4096, 4096).unwrap();
    let mut addrs: Vec<usize> = Vec::new();
    for _ in 0..N {
        addrs.push(unsafe { std::alloc::alloc_zeroed(layout) } as usize);
    }
    println!("pid={}", std::process::id());
    for a in &addrs {
        println!("page={:x}", a);
    }
    println!("storm_ms={}", storm_ms);
    std::io::stdout().flush().unwrap();

    // Four threads reading six pages each as fast as the machine allows -- no sleep, so the guard
    // churn is bounded only by the agent's own re-arm policy.
    let stop = Arc::new(AtomicBool::new(false));
    let mut handles = Vec::new();
    for t in 0..4usize {
        let pages = addrs.clone();
        let st = Arc::clone(&stop);
        handles.push(std::thread::spawn(move || {
            let mut i = t;
            while !st.load(Ordering::Relaxed) {
                for k in 0..6 {
                    let a = pages[(i + k) % pages.len()] as *const u8;
                    unsafe { std::ptr::read_volatile(a.add(0x40)) };
                }
                i += 1;
            }
        }));
    }
    std::thread::sleep(Duration::from_millis(storm_ms));
    stop.store(true, Ordering::Relaxed);
    for h in handles {
        let _ = h.join();
    }

    // A quiet gap before storing, so the heartbeat gets a window it cannot blame on the storm: the
    // stores must not land before the first heartbeat, or a caught store clears the interval and the
    // arm reports zero heartbeats for a reason that has nothing to do with starvation.
    std::thread::sleep(Duration::from_millis(8000));

    println!("storm over; storing");
    std::io::stdout().flush().unwrap();
    let mut n: u8 = 7;
    for (i, a) in addrs.iter().enumerate() {
        unsafe { std::ptr::write_bytes((*a as *mut u8).add(0x120), n, 32) };
        println!("write #{}", i);
        std::io::stdout().flush().unwrap();
        n = n.wrapping_add(1);
        std::thread::sleep(Duration::from_millis(200));
    }
    std::thread::sleep(Duration::from_secs(3));
}
