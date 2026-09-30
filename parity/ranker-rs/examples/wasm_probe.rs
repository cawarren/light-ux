//! wasm32 smoke/differential probe (not part of the library API). Exposes a tiny C ABI built
//! only from scalar arguments (no pointers, no unsafe blocks) so Node can drive the wasm build:
//! push UTF-16 units, commit items/query, score. Used by parity/ranker-ts/scripts/wasm-check.ts.
use ladder_rank::{Prepared, Query, Scorer};
use std::cell::RefCell;

thread_local! {
    static BUF: RefCell<Vec<u16>> = const { RefCell::new(Vec::new()) };
    static ITEMS: RefCell<Vec<Prepared>> = const { RefCell::new(Vec::new()) };
    static Q: RefCell<Option<Query>> = const { RefCell::new(None) };
    static SC: RefCell<Scorer> = RefCell::new(Scorer::new());
}

#[no_mangle]
pub extern "C" fn push_unit(u: u32) {
    BUF.with(|b| b.borrow_mut().push(u as u16));
}
#[no_mangle]
pub extern "C" fn commit_item() {
    let v = BUF.with(|b| std::mem::take(&mut *b.borrow_mut()));
    ITEMS.with(|i| i.borrow_mut().push(Prepared::new(v)));
}
#[no_mangle]
pub extern "C" fn commit_query() {
    let v = BUF.with(|b| std::mem::take(&mut *b.borrow_mut()));
    Q.with(|q| *q.borrow_mut() = Some(Query::new(v)));
}
/// Score of item `i` against the committed query; -1 for RangeError, -2 for TooLong.
#[no_mangle]
pub extern "C" fn score_item(i: u32) -> f64 {
    ITEMS.with(|items| {
        Q.with(|q| {
            SC.with(|sc| match sc.borrow_mut().score(&items.borrow()[i as usize], q.borrow().as_ref().unwrap()) {
                Ok(s) => s,
                Err(ladder_rank::ScoreError::JsRangeError) => -1.0,
                Err(ladder_rank::ScoreError::TooLong) => -2.0,
            })
        })
    })
}
/// Number of exponents 0..=POW_MAX where this target's f64::powf differs from the frozen table.
#[no_mangle]
pub extern "C" fn powf_diffs() -> u32 {
    (0..=ladder_rank::POW_MAX).filter(|&n| 0.999f64.powf(n as f64) != ladder_rank::pow_0999(n).unwrap()).count() as u32
}


