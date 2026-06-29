//! rbxl-engine — Roblox place/model → intermediate JSON tree (for extract.py)
//!
//! Reads a .rbxl/.rbxlx/.rbxm/.rbxmx file, decodes it with the rbx-dom crates,
//! and prints a nested JSON tree to stdout:
//!
//!   {"children": [ {"name","class","source"?,"children":[...]}, ... ]}
//!
//! `source` is only present for Script / LocalScript / ModuleScript instances.

use std::fs;
use std::path::PathBuf;

use rbx_dom_weak::WeakDom;
use rbx_types::{Ref, Variant};
use serde_json::{json, Value};

fn is_script(class: &str) -> bool {
    matches!(class, "Script" | "LocalScript" | "ModuleScript")
}

fn node(dom: &WeakDom, referent: Ref) -> Value {
    let inst = match dom.get_by_ref(referent) {
        Some(i) => i,
        None => return Value::Null,
    };

    let children: Vec<Value> = inst
        .children()
        .iter()
        .map(|&c| node(dom, c))
        .filter(|v| !v.is_null())
        .collect();

    let mut obj = json!({
        "name": inst.name,
        "class": inst.class,
        "children": children,
    });

    if is_script(&inst.class) {
        let source = match inst.properties.get("Source") {
            Some(Variant::String(s)) => s.clone(),
            Some(Variant::BinaryString(b)) => String::from_utf8_lossy(b.as_ref()).into_owned(),
            _ => String::new(),
        };
        obj["source"] = json!(source);
        // surface Disabled flag when present (Script/LocalScript only)
        if let Some(Variant::Bool(d)) = inst.properties.get("Disabled") {
            if *d {
                obj["disabled"] = json!(true);
            }
        }
    }

    obj
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: rbxl-engine <file.rbxl|.rbxlx|.rbxm|.rbxmx>");
        std::process::exit(2);
    }

    let path = PathBuf::from(&args[1]);
    let bytes = match fs::read(&path) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("rbxl-engine: cannot read {}: {}", path.display(), e);
            std::process::exit(1);
        }
    };

    let dom: WeakDom = if bytes.starts_with(b"<roblox!") {
        match rbx_binary::from_reader(bytes.as_slice()) {
            Ok(d) => d,
            Err(e) => {
                eprintln!("rbxl-engine: binary decode failed: {}", e);
                std::process::exit(3);
            }
        }
    } else {
        match rbx_xml::from_reader(bytes.as_slice(), rbx_xml::DecodeOptions::new()) {
            Ok(d) => d,
            Err(e) => {
                eprintln!("rbxl-engine: xml decode failed: {}", e);
                std::process::exit(3);
            }
        }
    };

    let root = dom.root();
    let top: Vec<Value> = root
        .children()
        .iter()
        .map(|&c| node(&dom, c))
        .filter(|v| !v.is_null())
        .collect();

    let out = json!({ "children": top });
    println!("{}", serde_json::to_string(&out).unwrap());
}
