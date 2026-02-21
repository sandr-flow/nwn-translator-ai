# NWN Modules Translator

AI-powered tool for translating Neverwinter Nights modules from any language to any language.

## Features

- **Provider-Agnostic Architecture**: Support for Grok (default), OpenAI, Gemini, and Mistral
- **Smart Token Preservation**: Game tokens like `<FirstName>`, `<Class>`, etc. are preserved
- **Context-Aware Translation**: Dialog trees are translated as complete units to maintain context
- **Batch Processing**: Translates all dialog, journal, item, and area descriptions in a module
- **Simple CLI**: Drop a .mod file, provide API key, get translated .mod file

## Installation

```bash
pip install nwn-modules-translator
```

## Usage

Basic translation:
```bash
nwn-translate module.mod --api-key YOUR_API_KEY --lang spanish
```

Specify output file:
```bash
nwn-translate module.mod --lang french -o module_fr.mod
```

Use different AI provider:
```bash
nwn-translate module.mod --lang russian --provider openai
```

## Supported Content Types

- Dialogs (.dlg) - Complete dialog trees with context
- Journal entries (.jrl) - Quest journals and categories
- Items (.uti) - Item names and descriptions
- Creatures (.utc) - Creature names and descriptions
- Areas (.are) - Area names and descriptions
- Placeables (.utt) - Placeable names and descriptions
- Doors (.utt) - Door names and descriptions
- Stores (.utm) - Store names and descriptions

## Token Preservation

The tool automatically preserves NWN game tokens:
- `<FirstName>`, `<LastName>`, `<Class>`, `<Race>`, `<Gender>`
- `<CustomToken:123>` - Custom token references
- All tokens are restored after translation

## AI Providers

- **Grok** (xAI) - Default, fast and affordable
- **OpenAI** - GPT-4 and GPT-3.5
- **Gemini** (Google) - Gemini Pro
- **Mistral** - Mistral AI models

## License

MIT License - See LICENSE file for details
