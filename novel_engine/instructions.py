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
DOCUMENT_DUPLICATES = {'docs/engine/agents/narrator.md': ['9f4b4ea120160be301e1c5a1956ac71b49ce5156bb5feab420c2a66dba270092', 'ae0cd840a1719030cf863d4f792e9fbea5815627212a3329aa6ebd435f752c72', '6f6eefef58dce19a62367c04fc68a0aed35d580c02fed09b777f41ef88bb55c0', '1b314d9fb00e05e2bce7e1f4f1297cc529ab419eb43bf8be23ab50810c915903', '64806267b3daca128e35624e0def0defadbf8df65ba3171f8f4d6ecadd618f1a', 'ed4be17e58d4febce2fffa610eea1e29a8b7762acf69e3922aba17764dcc33f7', 'be06aa8a04e33673ef98b1e042b0f04b0667eccf2f30418a5993a2dcc299a210', '2dcd150bdca1a015ee31372849f63f93d9c37287b21be2430aff2dc3db9238aa', 'e0e6484c49cf531f8fd1a88e5e9c93a65442374852f533c3f38767d6bb8557a1', '9ff74a9395bfd72916d82f32170d44074d5c7dff869e1dbf41a74f39a7ad5ef8', 'affdf15bbc3e3abe255dbd899c80e2604862ba901071cfbd7c90bb576c2ca7b8'], 'docs/engine/common_rules.md': ['db63eef7c0aedb2852aa19a690df2d6e8c8fc2ca5c16f0328fedc48f5232c9e6', '719cf0291d2ecd256d2522c63becfba46d4d2d52af51f846a806bfaf2b2474bb', '96916f5a1c7a6eef9d64509a04b2d19cd2099a02cfcb05794f09256a57607d83', '0d7f6fe3985cc1fec683c1e9dc22a23f0d693c7abf98325097c379e86bca8475', 'f8c24b946fd27b1cc82187538b8a87ecdfc97b9b32b3551eed1d02c64ebf39ef', 'bdf47452b55f986ed9ff62003b732fccc83e6e9b85c2b0980789a3bb9d7cf5c0', 'e41642697430da56784bf04c4bf3b505d4f5f51c0569714b6260e7b64d967715', 'e161db83b913d2fe221488cac5e4e8b4bfe0cd6ee44ebe81ea10b15714e4dfaf'], 'docs/engine/POSSESSIONS.md': []}

# New or modified paragraphs are kept in full.
KNOWN_DOCUMENT_PARAGRAPHS = {'docs/engine/agents/narrator.md': ['45739d4a2c480de66b4f4b03ea291168ea3a7bd7f6e178bcde6b8c874ea9b7e4', '9f4b4ea120160be301e1c5a1956ac71b49ce5156bb5feab420c2a66dba270092', 'ae0cd840a1719030cf863d4f792e9fbea5815627212a3329aa6ebd435f752c72', '4b050c12ec0d0a6e4858dc58373176fed95952776469723926459c453df9998f', '6f6eefef58dce19a62367c04fc68a0aed35d580c02fed09b777f41ef88bb55c0', '1b314d9fb00e05e2bce7e1f4f1297cc529ab419eb43bf8be23ab50810c915903', '64806267b3daca128e35624e0def0defadbf8df65ba3171f8f4d6ecadd618f1a', 'ed4be17e58d4febce2fffa610eea1e29a8b7762acf69e3922aba17764dcc33f7', 'be06aa8a04e33673ef98b1e042b0f04b0667eccf2f30418a5993a2dcc299a210', '2dcd150bdca1a015ee31372849f63f93d9c37287b21be2430aff2dc3db9238aa', 'e0e6484c49cf531f8fd1a88e5e9c93a65442374852f533c3f38767d6bb8557a1', '9ff74a9395bfd72916d82f32170d44074d5c7dff869e1dbf41a74f39a7ad5ef8', 'affdf15bbc3e3abe255dbd899c80e2604862ba901071cfbd7c90bb576c2ca7b8'], 'docs/engine/common_rules.md': ['cf17b533ed80de93c735182df8e4d4f0e0a17d408d1b43c80798964b070d901a', '0aeb08659ae997e58e9d38b144b243f66b98927cb3aa8fc0e14e0e6bcd298bd1', 'f3afda56086306dd212811fa4f5bc51d7c734935892603455cf3c421aae75047', '20f0832040bebe38f0daeb4bfc379c51729e350956e892e6fb6c356c173629d6', 'fdad9aa409b6a15f20abad269a129ccddacf2251f3555d8ab4ea39490eee604b', 'ed2b8f1c4d3f828664ec90a81305021f6e46c175e388eeb71374fd914b29957a', '2ad60a35f7926773197e8511b935b7efed2f0d7e2076697f8d1ba656755cb7a4', 'dc3ab3dd15a96f33380f3c2974fd91d24bed7fe7f1136c8454cd1c2f04502fb2', '91605d593d3b56565ba01a2dc6db770bb23814d2b34b824bfa83c94a0a1abc9e', 'db63eef7c0aedb2852aa19a690df2d6e8c8fc2ca5c16f0328fedc48f5232c9e6', '719cf0291d2ecd256d2522c63becfba46d4d2d52af51f846a806bfaf2b2474bb', '4992d7a8be3a41de62f5c2f32f4c32809784560c98fc0dc6b53b71d49bd2245a', '96916f5a1c7a6eef9d64509a04b2d19cd2099a02cfcb05794f09256a57607d83', '0d7f6fe3985cc1fec683c1e9dc22a23f0d693c7abf98325097c379e86bca8475', 'f8c24b946fd27b1cc82187538b8a87ecdfc97b9b32b3551eed1d02c64ebf39ef', 'bdf47452b55f986ed9ff62003b732fccc83e6e9b85c2b0980789a3bb9d7cf5c0', 'e41642697430da56784bf04c4bf3b505d4f5f51c0569714b6260e7b64d967715', 'edb775e94dcfc9cc76a4254bd184628106b477166542f04f88e84b2b7118c094', '8666374219a1ed9dc83a99c6fc112d0cf3a2438e1b3b438a9225d5906a5bbb7d', '6302818e5c113e78414cb95841e4effe1d4e73ed6b4ff68e003223304ff5b634', 'e161db83b913d2fe221488cac5e4e8b4bfe0cd6ee44ebe81ea10b15714e4dfaf'], 'docs/engine/POSSESSIONS.md': ['d3cc14950b6e14019009797540f72ddfadd6cb268aa2549727360ca343b8d77d', '5fb857a7a4f495f9ef8eb81e9822121adf1bca2fdd40c7961972c814b2425d09', '975539506130ca126f98f056f5a60b641800c2ebeabf4f6f49098fc26b05cd51', '8465e05e82d172e6fb61d545317111645ec359a90ce68abc647027d1f2cf369b', '4b05c15a8e4b587e1e5acb45766177d215fc5de2dcbeda2f30837e9b6f35b8e8', '2d747e5f3b673da0b6965beecdc05947177e40aabde0f5f8b14d74d11e30f4be', 'ffcbfbce0916449d5b43d52cd4ea6382a52d7927b7978f33df0dcc8fca2c67fc', '3e140f554f3d8982df467ff26dcaf03853cdc828990750df0f01ffb7bbdc8e5d', '18e4ec40f07be27b99a353b7b6f98818be1b897fce1562b2cebe6625a7bcb2a6', 'fcfd91ca588cb50b3e45d7f9304d0df1a3b6faf456dc17040e8df233556a2267', 'e14826143c55de69166ef32a024af3dd63d88f5a1bcdf8ad60aeafb1fe331db5', '5b377a2d3cc4e058a03edefd77bc2e432cd853b3eee8be51ae65dd353e5b755a', 'd2cd677d60c23022db9682f6b1f28b7497448cfcea08b24b29233db69e912bfa', 'bef0507f68008853ba5531313704fa7b377859ace4c9bc3d72e6ad21f3fe9be3', '336628478a1e1a9546870f8633dfb3f9a394c1fdc7b03b5447f3ccfe93be7eac', 'a6d40ac601e703f22c6123be8ab73d2eaf10a9129133b875801108f8d43c1897']}
