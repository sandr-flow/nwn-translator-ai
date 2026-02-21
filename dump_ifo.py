import sys
from pathlib import Path
from src.nwn_translator.file_handlers.gff_handler import read_gff
from src.nwn_translator.file_handlers.erf_reader import ERFReader

def dump_gff(file_path, output_path):
    print(f"Reading {file_path}")
    reader = ERFReader(file_path)
    reader.read_entries()
    
    print(f"Total entries: {len(reader.entries)}")
    for i, entry in enumerate(reader.entries):
        if i < 10:
            print(f"  Entry {i}: {entry.res_ref} (type: {hex(entry.res_type)})")
            
        if entry.res_ref.lower() == "module" or entry.res_type == 0x07D5:
            print(f"  FOUND POTENTIAL IFO: {entry.res_ref} (type: {hex(entry.res_type)})")
            with open(file_path, "rb") as f:
                f.seek(entry.offset)
                ifo_data = f.read(entry.size)
            
    if not ifo_data:
        print("module.ifo not found!")
        return

    import struct
    import json
    
    # Save raw ifo
    with open(output_path + ".ifo", "wb") as f:
        f.write(ifo_data)
        
    print(f"Saved {output_path}.ifo")

if __name__ == "__main__":
    orig_mod = Path("test_modules/The Dark Ranger's Treasure.mod")
    trans_mod = Path("test_modules/The Dark Ranger's Treasure_rus.mod")
    
    dump_gff(orig_mod, "module_orig")
    dump_gff(trans_mod, "module_trans")
