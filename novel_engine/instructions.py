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
DOCUMENT_DUPLICATES = {'docs/engine/agents/narrator.md': ['9f4b4ea120160be301e1c5a1956ac71b49ce5156bb5feab420c2a66dba270092', 'ae0cd840a1719030cf863d4f792e9fbea5815627212a3329aa6ebd435f752c72', '6f6eefef58dce19a62367c04fc68a0aed35d580c02fed09b777f41ef88bb55c0', '1b314d9fb00e05e2bce7e1f4f1297cc529ab419eb43bf8be23ab50810c915903', '64806267b3daca128e35624e0def0defadbf8df65ba3171f8f4d6ecadd618f1a', 'ed4be17e58d4febce2fffa610eea1e29a8b7762acf69e3922aba17764dcc33f7', 'be06aa8a04e33673ef98b1e042b0f04b0667eccf2f30418a5993a2dcc299a210', '2dcd150bdca1a015ee31372849f63f93d9c37287b21be2430aff2dc3db9238aa', 'e0e6484c49cf531f8fd1a88e5e9c93a65442374852f533c3f38767d6bb8557a1', '9ff74a9395bfd72916d82f32170d44074d5c7dff869e1dbf41a74f39a7ad5ef8', 'affdf15bbc3e3abe255dbd899c80e2604862ba901071cfbd7c90bb576c2ca7b8'], 'docs/engine/common_rules.md': ['17c31be5cf0c31564dd4c334fa0da82c5ae8d9b7416fed9fb96a84d5c9caba7a', 'd61c5cea6b73f6151bafb9ec9c63702bc91ac9ca9b8c44bbdc865fcd04fdd87c', 'ceef20e2bac7fb2093d3dca476d66e38bb5549aae4dad8f68826f60dda7ea6f0', 'dab2532aeb4a6025cc25c8ec4eae195e4888105bec36a5d32f34e092ca3049fb', 'f8c24b946fd27b1cc82187538b8a87ecdfc97b9b32b3551eed1d02c64ebf39ef', 'defe6c45f62c9fa3a72d1d4829005d35be606aa192c4c5e565659519741e245d', 'e41642697430da56784bf04c4bf3b505d4f5f51c0569714b6260e7b64d967715', 'e161db83b913d2fe221488cac5e4e8b4bfe0cd6ee44ebe81ea10b15714e4dfaf'], 'docs/engine/POSSESSIONS.md': []}

# New or modified paragraphs are kept in full.
KNOWN_DOCUMENT_PARAGRAPHS = {'docs/engine/agents/narrator.md': ['45739d4a2c480de66b4f4b03ea291168ea3a7bd7f6e178bcde6b8c874ea9b7e4', '9f4b4ea120160be301e1c5a1956ac71b49ce5156bb5feab420c2a66dba270092', 'ae0cd840a1719030cf863d4f792e9fbea5815627212a3329aa6ebd435f752c72', '4b050c12ec0d0a6e4858dc58373176fed95952776469723926459c453df9998f', '6f6eefef58dce19a62367c04fc68a0aed35d580c02fed09b777f41ef88bb55c0', '1b314d9fb00e05e2bce7e1f4f1297cc529ab419eb43bf8be23ab50810c915903', '64806267b3daca128e35624e0def0defadbf8df65ba3171f8f4d6ecadd618f1a', 'ed4be17e58d4febce2fffa610eea1e29a8b7762acf69e3922aba17764dcc33f7', 'be06aa8a04e33673ef98b1e042b0f04b0667eccf2f30418a5993a2dcc299a210', '2dcd150bdca1a015ee31372849f63f93d9c37287b21be2430aff2dc3db9238aa', 'e0e6484c49cf531f8fd1a88e5e9c93a65442374852f533c3f38767d6bb8557a1', '9ff74a9395bfd72916d82f32170d44074d5c7dff869e1dbf41a74f39a7ad5ef8', 'affdf15bbc3e3abe255dbd899c80e2604862ba901071cfbd7c90bb576c2ca7b8'], 'docs/engine/common_rules.md': ['cf17b533ed80de93c735182df8e4d4f0e0a17d408d1b43c80798964b070d901a', '0aeb08659ae997e58e9d38b144b243f66b98927cb3aa8fc0e14e0e6bcd298bd1', 'f3afda56086306dd212811fa4f5bc51d7c734935892603455cf3c421aae75047', '743034c8d51fcb30492edbcc24f86b1ff590faecb5ee7f7d7d8770afdba4993c', 'fdad9aa409b6a15f20abad269a129ccddacf2251f3555d8ab4ea39490eee604b', 'ed2b8f1c4d3f828664ec90a81305021f6e46c175e388eeb71374fd914b29957a', '2ad60a35f7926773197e8511b935b7efed2f0d7e2076697f8d1ba656755cb7a4', 'dc0bc8344ba50528e2cdef274e970f0303a83d1eec86c47a4c4d8b4fb2ca4f00', '91605d593d3b56565ba01a2dc6db770bb23814d2b34b824bfa83c94a0a1abc9e', '17c31be5cf0c31564dd4c334fa0da82c5ae8d9b7416fed9fb96a84d5c9caba7a', 'd61c5cea6b73f6151bafb9ec9c63702bc91ac9ca9b8c44bbdc865fcd04fdd87c', '4992d7a8be3a41de62f5c2f32f4c32809784560c98fc0dc6b53b71d49bd2245a', 'ceef20e2bac7fb2093d3dca476d66e38bb5549aae4dad8f68826f60dda7ea6f0', 'dab2532aeb4a6025cc25c8ec4eae195e4888105bec36a5d32f34e092ca3049fb', 'f8c24b946fd27b1cc82187538b8a87ecdfc97b9b32b3551eed1d02c64ebf39ef', 'defe6c45f62c9fa3a72d1d4829005d35be606aa192c4c5e565659519741e245d', 'e41642697430da56784bf04c4bf3b505d4f5f51c0569714b6260e7b64d967715', 'edb775e94dcfc9cc76a4254bd184628106b477166542f04f88e84b2b7118c094', '8666374219a1ed9dc83a99c6fc112d0cf3a2438e1b3b438a9225d5906a5bbb7d', '4d86833ab534bbee7be0939e791264ca73cee5b7e3db66fa83c792af91f2f547', 'e161db83b913d2fe221488cac5e4e8b4bfe0cd6ee44ebe81ea10b15714e4dfaf'], 'docs/engine/POSSESSIONS.md': ['d3cc14950b6e14019009797540f72ddfadd6cb268aa2549727360ca343b8d77d', '5fb857a7a4f495f9ef8eb81e9822121adf1bca2fdd40c7961972c814b2425d09', '975539506130ca126f98f056f5a60b641800c2ebeabf4f6f49098fc26b05cd51', '8465e05e82d172e6fb61d545317111645ec359a90ce68abc647027d1f2cf369b', '4b05c15a8e4b587e1e5acb45766177d215fc5de2dcbeda2f30837e9b6f35b8e8', '2d747e5f3b673da0b6965beecdc05947177e40aabde0f5f8b14d74d11e30f4be', 'ffcbfbce0916449d5b43d52cd4ea6382a52d7927b7978f33df0dcc8fca2c67fc', '3e140f554f3d8982df467ff26dcaf03853cdc828990750df0f01ffb7bbdc8e5d', '18e4ec40f07be27b99a353b7b6f98818be1b897fce1562b2cebe6625a7bcb2a6', 'fcfd91ca588cb50b3e45d7f9304d0df1a3b6faf456dc17040e8df233556a2267', 'e14826143c55de69166ef32a024af3dd63d88f5a1bcdf8ad60aeafb1fe331db5', '5b377a2d3cc4e058a03edefd77bc2e432cd853b3eee8be51ae65dd353e5b755a', 'd2cd677d60c23022db9682f6b1f28b7497448cfcea08b24b29233db69e912bfa', '3dba208f873bd565bcabca200215106114b22cb37362881d912c49c9f90eed65', '336628478a1e1a9546870f8633dfb3f9a394c1fdc7b03b5447f3ccfe93be7eac', '5036a87da139ad88833aef71e490dd7b03d660ffe53c679094793d30055aee92']}
