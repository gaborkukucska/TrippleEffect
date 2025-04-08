// static/js/modules/swipe.js (v7 - Debug Overshoot)

// ... (state variables, initSwipe, handleTouchStart, handleTouchMove - unchanged from v6) ...
let contentWrapper = null;
let swipeSections = null;
let numSections = 0;
let touchStartX = 0;
let touchStartY = 0;
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false;
const swipeThreshold = 50;
let getCurrentSectionIndexCallback = () => 0;
let setCurrentSectionIndexCallback = (index) => {};

export function initSwipe(wrapper, sections, getIndexFunc, setIndexFunc) {
    contentWrapper = wrapper;
    swipeSections = sections;
    numSections = sections.length;
    getCurrentSectionIndexCallback = getIndexFunc;
    setCurrentSectionIndexCallback = setIndexFunc;
    console.log(`Swipe module initialized with ${numSections} sections.`);
    updateContentWrapperTransform(getCurrentSectionIndexCallback(), false);
}

export function handleTouchStart(event) {
    if (!contentWrapper) return;
    const target = event.target;
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    const isInsideScrollable = target.closest('.message-area, #agent-status-content, #config-content');
    const isModalOpen = document.querySelector('.modal[style*="display: block"]');

    if (isInteractive || isModalOpen || isInsideScrollable) {
        isSwiping = false;
        console.log(`Swipe ignored (target: ${target.tagName}, interact: ${!!isInteractive}, insideScroll: ${!!isInsideScrollable}, modal: ${!!isModalOpen})`);
        return;
    }
    try {
        touchStartX = event.touches[0].clientX;
        touchStartY = event.touches[0].clientY;
        touchCurrentX = touchStartX;
        isSwiping = true;
        horizontalSwipeConfirmed = false;
        contentWrapper.style.transition = 'none';
        console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
    } catch (e) { console.error("Error in touchstart:", e); isSwiping = false; }
}

export function handleTouchMove(event) {
    if (!isSwiping || !contentWrapper) return;
    try {
        const currentY = event.touches[0].clientY;
        touchCurrentX = event.touches[0].clientX;
        const diffX = touchCurrentX - touchStartX;
        const diffY = currentY - touchStartY;

        if (!horizontalSwipeConfirmed) {
            if (Math.abs(diffX) > Math.abs(diffY) + 5) {
                horizontalSwipeConfirmed = true; console.log("Horizontal swipe confirmed.");
            } else if (Math.abs(diffY) > Math.abs(diffX) + 5) {
                isSwiping = false; console.log("Vertical scroll detected, canceling swipe."); return;
            }
        }
        if (horizontalSwipeConfirmed) {
            event.preventDefault();
            const currentIdx = getCurrentSectionIndexCallback();
            const baseTranslateXPercent = -currentIdx * 100;
            contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    } catch (e) { console.error("Error in touchmove:", e); isSwiping = false; }
}

/**
 * TouchEnd event handler - **REFINED LOGIC V7**
 * @param {TouchEvent} event
 */
export function handleTouchEnd(event) {
    if (!isSwiping || !contentWrapper) {
        isSwiping = false; horizontalSwipeConfirmed = false; return;
    }

    const wasHorizontal = horizontalSwipeConfirmed;
    // --- Reset flags immediately ---
    isSwiping = false;
    horizontalSwipeConfirmed = false;

    let currentIdx = getCurrentSectionIndexCallback();
    let targetIndex = currentIdx; // Default to current

    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd (H): Index BEFORE=${currentIdx}, diffX=${diffX.toFixed(0)}, thres=${swipeThreshold}`);

        // Determine target index based on swipe
        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0) { // Swipe Left
                targetIndex = currentIdx + 1; // Intend to go next
                console.log(`Swipe Left -> Target Index Proposed: ${targetIndex}`);
            } else { // Swipe Right (diffX > 0)
                targetIndex = currentIdx - 1; // Intend to go previous
                console.log(`Swipe Right -> Target Index Proposed: ${targetIndex}`);
            }
            // --- CLAMP the target index ---
            targetIndex = Math.max(0, Math.min(targetIndex, numSections - 1));
            console.log(`Target Index Clamped: ${targetIndex}`);

        } else {
            console.log("Swipe distance below threshold. Target Index remains:", targetIndex);
            // Target index remains currentIdx, no need to clamp here
        }
    } else {
         console.log("TouchEnd: No horizontal swipe confirmed. Target Index remains:", targetIndex);
         // Target index remains currentIdx
    }

    // --- Update state in app.js ---
    console.log(`TouchEnd: Calling setIndex(${targetIndex})`);
    setCurrentSectionIndexCallback(targetIndex); // Update the state managed by app.js

    // --- Apply the final transform WITH transition ---
    // Pass the *already clamped* targetIndex
    updateContentWrapperTransform(targetIndex, true);
}


/**
 * Updates the CSS transform. Ensures transition is set correctly.
 * @param {number} index - The index of the section to display (SHOULD BE ALREADY CLAMPED).
 * @param {boolean} [useTransition=true]
 */
export function updateContentWrapperTransform(index, useTransition = true) {
    // Log the index received
    console.log(`updateContentWrapperTransform called with index=${index}, useTransition=${useTransition}`);

    // Re-Clamp index here as a safety measure, though it should be clamped before calling.
    const clampedIndex = Math.max(0, Math.min(numSections - 1, index));
    if (clampedIndex !== index) {
         console.warn(`Index ${index} was clamped to ${clampedIndex} in updateContentWrapperTransform!`);
    }

    if (contentWrapper && typeof clampedIndex === 'number') {
        const newTranslateXPercent = -clampedIndex * 100;
        console.log(`---> Applying transform: Final Index=${clampedIndex}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);

        // Set transition *before* setting the final transform when animating
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-out' : 'none'; // Ensure using ease-out
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;

        // Add a small delay then check the final transform value (for debugging)
        if (useTransition) {
            setTimeout(() => {
                const currentTransform = window.getComputedStyle(contentWrapper).transform;
                console.log(`---> Transform AFTER 350ms: Index=${clampedIndex}, Style=${currentTransform}`);
            }, 350); // Just after transition should end
        }

    } else {
        console.error(`Invalid call to updateContentWrapperTransform: Index=${index}, Clamped=${clampedIndex}, Wrapper=${!!contentWrapper}`);
    }
}

/**
 * Adds keyboard navigation listeners.
 * @param {function} getIndex
 * @param {function} setIndex
 */
export function addKeyboardNavListeners(getIndex, setIndex) {
     document.addEventListener('keydown', (e) => {
        const targetTagName = document.activeElement?.tagName.toLowerCase();
        const isModalOpen = document.querySelector('.modal[style*="display: block"]');
        const isInputFocused = ['textarea', 'input', 'select'].includes(targetTagName);
        if (isModalOpen || isInputFocused) return;

        let currentIndex = getIndex();
        let newIndex = currentIndex;

        if (e.key === 'ArrowLeft' && currentIndex > 0) {
            newIndex = currentIndex - 1;
            console.log(`Key Left -> New Index: ${newIndex}`);
        } else if (e.key === 'ArrowRight' && currentIndex < numSections - 1) {
            newIndex = currentIndex + 1;
             console.log(`Key Right -> New Index: ${newIndex}`);
        }

        if (newIndex !== currentIndex) {
            setIndex(newIndex); // Update state in app.js
            updateContentWrapperTransform(newIndex, true); // Update view
        }
    });
    console.log("Keyboard navigation listeners added.");
}
