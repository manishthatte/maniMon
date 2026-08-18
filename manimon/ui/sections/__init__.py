"""
One module per panel section, each owning both halves of its job: the widgets
it creates and the code that fills them.

Before this split those two halves lived 150 lines apart in a single module,
which is why a widget could be created and never updated without anything
noticing.
"""
