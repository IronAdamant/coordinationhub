//! Process-safe hub. Each call loads the snapshot, mutates it, and writes it back.

use crate::call::{self, CallError};
use crate::json::Json;
use crate::model::State;
use crate::paths::{detect_project_root, posix};
use crate::store::{FileGuard, Store};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

struct Inner {
    state: Option<State>,
    file: Option<FileGuard>,
}

pub struct Hub {
    store: Store,
    pub root: PathBuf,
    pub namespace: String,
    inner: Mutex<Inner>,
}

impl Hub {
    pub fn open(storage: Option<&Path>, project: Option<&Path>, namespace: &str) -> std::io::Result<Hub> {
        let root = project
            .map(|p| p.to_path_buf())
            .or_else(|| detect_project_root(&std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))))
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
        let dir = storage
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| root.join(".coordinationhub"));
        let store = Store::open(&dir)?;
        Ok(Hub {
            store,
            root,
            namespace: namespace.to_string(),
            inner: Mutex::new(Inner {
                state: None,
                file: None,
            }),
        })
    }

    pub fn storage_dir(&self) -> &Path {
        &self.store.dir
    }

    fn ensure(&self, inner: &mut Inner) {
        if inner.file.is_none() {
            inner.file = Some(self.store.lock());
            inner.state = Some(self.store.load());
        }
    }

    pub fn read<T>(&self, f: impl FnOnce(&State) -> T) -> T {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        self.ensure(&mut inner);
        f(inner.state.as_ref().unwrap())
    }

    pub fn transact<T>(&self, f: impl FnOnce(&mut State) -> T) -> T {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        self.ensure(&mut inner);
        let state = inner.state.as_mut().unwrap();
        let out = f(state);
        let _ = self.store.save(state);
        out
    }

    pub fn call(&self, name: &str, args: &Json) -> Result<Json, CallError> {
        let root = self.root.clone();
        let readonly = call::is_read_only(name);
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        self.ensure(&mut inner);
        let state = inner.state.as_mut().unwrap();
        let out = call::call(state, &root, name, args);
        if !readonly {
            let _ = self.store.save(state);
        }
        out
    }

    pub fn root_display(&self) -> String {
        posix(&self.root)
    }
}
