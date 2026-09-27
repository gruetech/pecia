//! Order-preserving parallel map over an index range, on scoped threads.
//!
//! The work pecia repeats per line — parse, validate, hash — is independent
//! line by line; only what links lines (seq, prev) needs them in order. This
//! is the one place that fans that work across the cores. Chunks are pulled
//! from a shared counter rather than dealt out up front, so a slower core
//! (the efficiency cores of a laptop) takes fewer of them.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::OnceLock;

/// Below this many items the threads cost more than they save.
const SERIAL_BELOW: usize = 64;

fn threads() -> usize {
    static N: OnceLock<usize> = OnceLock::new();
    *N.get_or_init(|| std::thread::available_parallelism().map_or(1, |n| n.get()))
}

/// `(0..n).map(f).collect()`, computed on every core, in index order.
pub fn map<R: Send>(n: usize, f: impl Fn(usize) -> R + Sync) -> Vec<R> {
    let threads = threads();
    if n < SERIAL_BELOW || threads == 1 {
        return (0..n).map(f).collect();
    }
    let chunk = n.div_ceil(threads * 4).max(SERIAL_BELOW / 4);
    let chunks = n.div_ceil(chunk);
    let next = AtomicUsize::new(0);
    let mut done: Vec<(usize, Vec<R>)> = std::thread::scope(|s| {
        let workers: Vec<_> = (0..threads.min(chunks))
            .map(|_| {
                let (f, next) = (&f, &next);
                s.spawn(move || {
                    let mut mine = Vec::new();
                    loop {
                        let c = next.fetch_add(1, Ordering::Relaxed);
                        if c >= chunks {
                            return mine;
                        }
                        mine.push((c, (c * chunk..((c + 1) * chunk).min(n)).map(f).collect::<Vec<R>>()));
                    }
                })
            })
            .collect();
        workers.into_iter().flat_map(|w| w.join().expect("a worker panicked")).collect()
    });
    done.sort_unstable_by_key(|(c, _)| *c);
    done.into_iter().flat_map(|(_, part)| part).collect()
}

#[cfg(test)]
mod tests {
    #[test]
    fn keeps_order_at_every_size() {
        for n in [0, 1, 63, 64, 65, 1000, 100_003] {
            assert_eq!(super::map(n, |i| i * 3), (0..n).map(|i| i * 3).collect::<Vec<_>>(), "{n}");
        }
    }
}
