//! gnomon-core: the zero-dependency heart of Gnomon.
//!
//! One append-only, hash-chained log is the source of truth. Everything else
//! (the current state, views, indexes) is derived from it and can be rebuilt.
//!
//! * [`json`]: strict parsing and RFC 8785 canonical form
//! * [`sha256`]: hashing
//! * [`schema`]: claim schema v0.1 and body validation
//! * [`ledger`]: the write gate and derived state
//! * [`store`]: the log on disk
//! * [`ffi`]: a C ABI used by the Python package (via ctypes)

pub mod ffi;
pub mod json;
pub mod ledger;
pub mod schema;
pub mod sha256;
pub mod store;
