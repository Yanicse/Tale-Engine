"""Narrative instructions grouped by responsibility, with no manual computation installed."""
from .prompts import load as load_prompt

CHRONICLE_REPORTS = load_prompt('instructions.chronicle_reports')

OPENING = load_prompt('instructions.opening')

DIALOGUE = load_prompt('instructions.dialogue')

CANON = load_prompt('instructions.canon')

def narration_language_block(language):
    return ('NARRATION LANGUAGE: write the narration and every free-text field in ' + str(language) +
            '. Keep names and quotations in their source language. ')


STORY = load_prompt('instructions.story')

WORLD_UNKNOWNS = load_prompt('instructions.world_unknowns')

ARCS = load_prompt('instructions.arcs')

WORLD = load_prompt('instructions.world')

TRANSPORT = load_prompt('instructions.transport')

POSSESSIONS = load_prompt('instructions.possessions')

LOCAL_SHEETS = load_prompt('instructions.local_sheets')

RELATIONS = load_prompt('instructions.relations')

TRANSFERS = load_prompt('instructions.transfers')

MEMORY = load_prompt('instructions.memory')

OLD_MECHANICS = load_prompt('instructions.old_mechanics')

STRUCTURE = load_prompt('instructions.structure')

AGENDA = load_prompt('instructions.agenda')

MECHANICS = load_prompt('instructions.mechanics')

# Technical paragraphs already represented in the modules; any modification is kept.
DOCUMENT_DUPLICATES = {'docs/engine/agents/narrator.md': ['9f4b4ea120160be301e1c5a1956ac71b49ce5156bb5feab420c2a66dba270092', 'ae0cd840a1719030cf863d4f792e9fbea5815627212a3329aa6ebd435f752c72', 'a4f00d6a43decdd023b7deb633aba1242b32ed0bca05794de00ac99e6b47b6f5', '0f830f00ab1f5461afe63fc4bfcf5e7b69ce8cf0c5b879d077408ff2ec82b647', '1f5273aac4c48d9a01f3935c7349324e4b5248d0b7931041138c86afa50a964a', '29ed487ac97d6568d474945a270a66ce91022fa3600512eb191da0c4deb64b4b', 'be06aa8a04e33673ef98b1e042b0f04b0667eccf2f30418a5993a2dcc299a210', '2dcd150bdca1a015ee31372849f63f93d9c37287b21be2430aff2dc3db9238aa', 'c0553392b8e415fb83c0be1520798db3a47c670c3236461b46101c9eb3f4d14d', '9ff74a9395bfd72916d82f32170d44074d5c7dff869e1dbf41a74f39a7ad5ef8', '1f74535252da2ff82b30b27d94ec8a8307ea3d1d20b90030858587a8f005390f'], 'docs/engine/common_rules.md': ['db63eef7c0aedb2852aa19a690df2d6e8c8fc2ca5c16f0328fedc48f5232c9e6', '719cf0291d2ecd256d2522c63becfba46d4d2d52af51f846a806bfaf2b2474bb', '4dc3cb83f490425023c029873cd6ca12e419cf733a9656ae878cb5bd6484025a', '0d7f6fe3985cc1fec683c1e9dc22a23f0d693c7abf98325097c379e86bca8475', 'f2d1400f6aa47e8f70599e9d03d2202029a758a6d8853290d5bcc7308384e54c', 'bdf47452b55f986ed9ff62003b732fccc83e6e9b85c2b0980789a3bb9d7cf5c0', 'b6573563a058f05a490e32f59c2a909ebd0b076bfa4c675346a566ac65d5a85b', 'e161db83b913d2fe221488cac5e4e8b4bfe0cd6ee44ebe81ea10b15714e4dfaf'], 'docs/engine/POSSESSIONS.md': []}

# New or modified paragraphs are kept in full.
KNOWN_DOCUMENT_PARAGRAPHS = {'docs/engine/agents/narrator.md': ['45739d4a2c480de66b4f4b03ea291168ea3a7bd7f6e178bcde6b8c874ea9b7e4', '9f4b4ea120160be301e1c5a1956ac71b49ce5156bb5feab420c2a66dba270092', 'ae0cd840a1719030cf863d4f792e9fbea5815627212a3329aa6ebd435f752c72', '4b050c12ec0d0a6e4858dc58373176fed95952776469723926459c453df9998f', 'a4f00d6a43decdd023b7deb633aba1242b32ed0bca05794de00ac99e6b47b6f5', '0f830f00ab1f5461afe63fc4bfcf5e7b69ce8cf0c5b879d077408ff2ec82b647', '1f5273aac4c48d9a01f3935c7349324e4b5248d0b7931041138c86afa50a964a', '29ed487ac97d6568d474945a270a66ce91022fa3600512eb191da0c4deb64b4b', 'be06aa8a04e33673ef98b1e042b0f04b0667eccf2f30418a5993a2dcc299a210', '2dcd150bdca1a015ee31372849f63f93d9c37287b21be2430aff2dc3db9238aa', 'c0553392b8e415fb83c0be1520798db3a47c670c3236461b46101c9eb3f4d14d', '9ff74a9395bfd72916d82f32170d44074d5c7dff869e1dbf41a74f39a7ad5ef8', '1f74535252da2ff82b30b27d94ec8a8307ea3d1d20b90030858587a8f005390f'], 'docs/engine/common_rules.md': ['cf17b533ed80de93c735182df8e4d4f0e0a17d408d1b43c80798964b070d901a', '0aeb08659ae997e58e9d38b144b243f66b98927cb3aa8fc0e14e0e6bcd298bd1', 'f3afda56086306dd212811fa4f5bc51d7c734935892603455cf3c421aae75047', '20f0832040bebe38f0daeb4bfc379c51729e350956e892e6fb6c356c173629d6', '58dacbea11990bbb5278e16900fd9a2ab5eaa4fabb4dd88bc33908750b3ff165', 'ed2b8f1c4d3f828664ec90a81305021f6e46c175e388eeb71374fd914b29957a', '2ad60a35f7926773197e8511b935b7efed2f0d7e2076697f8d1ba656755cb7a4', '59aff5cf905ee92134bbaa5dc82867c48251ee6f0010c66f2216d7c66ce7b541', '91605d593d3b56565ba01a2dc6db770bb23814d2b34b824bfa83c94a0a1abc9e', 'db63eef7c0aedb2852aa19a690df2d6e8c8fc2ca5c16f0328fedc48f5232c9e6', '719cf0291d2ecd256d2522c63becfba46d4d2d52af51f846a806bfaf2b2474bb', '4992d7a8be3a41de62f5c2f32f4c32809784560c98fc0dc6b53b71d49bd2245a', '4dc3cb83f490425023c029873cd6ca12e419cf733a9656ae878cb5bd6484025a', '0d7f6fe3985cc1fec683c1e9dc22a23f0d693c7abf98325097c379e86bca8475', 'f2d1400f6aa47e8f70599e9d03d2202029a758a6d8853290d5bcc7308384e54c', 'bdf47452b55f986ed9ff62003b732fccc83e6e9b85c2b0980789a3bb9d7cf5c0', 'b6573563a058f05a490e32f59c2a909ebd0b076bfa4c675346a566ac65d5a85b', 'edb775e94dcfc9cc76a4254bd184628106b477166542f04f88e84b2b7118c094', '43acf20ed09369b9c22d1de25d58566ea1a23dbf81f468d55405dc5a2db60a60', '4c4aad5e2189ca1ff3ac68306005d97c8d0160c0b6ac608166c391e0b8d0c60f', 'e161db83b913d2fe221488cac5e4e8b4bfe0cd6ee44ebe81ea10b15714e4dfaf'], 'docs/engine/POSSESSIONS.md': ['d3cc14950b6e14019009797540f72ddfadd6cb268aa2549727360ca343b8d77d', '76fc02b18e1042bb811ea65a6ecb0a0a120371190e69a5116c8793da0232d6f9', '975539506130ca126f98f056f5a60b641800c2ebeabf4f6f49098fc26b05cd51', 'ab3f59e7a95eb32367cf752835997c16d757736ff6c221a8918139d2acc7f943', '4b05c15a8e4b587e1e5acb45766177d215fc5de2dcbeda2f30837e9b6f35b8e8', 'fb6be99c57160f166f9be2aa503720fde4eb55a661bbef5ef853bed7596bbf8c', 'ffcbfbce0916449d5b43d52cd4ea6382a52d7927b7978f33df0dcc8fca2c67fc', 'f44b184a8b4431b68a400ca9af117a5eb63a266049e8cf8e4f13209dbb384950', '18e4ec40f07be27b99a353b7b6f98818be1b897fce1562b2cebe6625a7bcb2a6', 'fcfd91ca588cb50b3e45d7f9304d0df1a3b6faf456dc17040e8df233556a2267', '45fe9f7a3668815e136dd6fa270db76062d35fa2eebfe8b26fb87a56b9255e18', 'a723b23d1c0bcf07371bb690caebe62d979c087ff5d2fc526825c9c893625153', 'd2cd677d60c23022db9682f6b1f28b7497448cfcea08b24b29233db69e912bfa', 'bef0507f68008853ba5531313704fa7b377859ace4c9bc3d72e6ad21f3fe9be3', 'eaa587cb34ee569604872dc6d45be9f9e34b38c8d422dc4da9cb4d815605b1d7', 'a6d40ac601e703f22c6123be8ab73d2eaf10a9129133b875801108f8d43c1897']}
