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
//! * [`snapshot`]: verified state saved beside the log, for fast starts
//! * [`datalog`]: a small, exact query engine with proofs
//! * [`query`]: the memory as facts, built-in views, queries
//! * [`recall`]: ranked recall of what was said, with exact dates
//! * [`ffi`]: a C ABI used by the Python package (via ctypes)

pub mod datalog;
pub mod ffi;
pub mod json;
pub mod ledger;
pub mod query;
pub mod recall;
pub mod schema;
pub mod sha256;
pub mod snapshot;
pub mod store;
